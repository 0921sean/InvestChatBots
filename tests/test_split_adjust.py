"""액면분할 자동 반영(#170) — 진입 후 분할 감지 시 진입가·수량·손절가 조정, 멱등성 보장.
yfinance는 가짜 splits로 대체(네트워크 없음)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pandas as pd

TEST_DB = "test_split_adjust.db"


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    from db import init_db, ensure_desk_accounts
    init_db()
    ensure_desk_accounts()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


class _FakeTicker:
    """yf.Ticker 대역 — splits만 흉내."""
    split_map = {}

    def __init__(self, code):
        self.code = code

    @property
    def splits(self):
        return self.split_map.get(self.code, pd.Series(dtype=float))


def _make_position(code="APH", entry=167.23, amount=2_500_000.0, stop=None):
    from db import buy_shared_position, _conn
    pos_id, err = buy_shared_position(code, code, entry, amount, "테스트", market="US", account="발굴주")
    assert err is None
    with _conn() as con:
        # opened_at을 분할 이전으로 고정 + 필요시 손절가
        con.execute("UPDATE virtual_positions SET opened_at='2026-08-11 06:00:00', stop_price=? WHERE id=?",
                    (stop, pos_id))
    return pos_id


def _get(pos_id):
    from db import _conn
    import sqlite3
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return dict(con.execute("SELECT * FROM virtual_positions WHERE id=?", (pos_id,)).fetchone())


def _fake_yf(monkeypatch, split_map):
    import aifund
    _FakeTicker.split_map = split_map
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)


def test_split_after_entry_adjusts_entry_qty_stop(monkeypatch):
    import aifund
    pos_id = _make_position(stop=150.0)
    before = _get(pos_id)
    _fake_yf(monkeypatch, {"APH": pd.Series([2.0], index=[pd.Timestamp("2026-09-03", tz="America/New_York")])})
    out = aifund.run_split_adjust()
    assert [a["code"] for a in out["adjusted"]] == ["APH"]
    after = _get(pos_id)
    assert after["entry_price"] == pytest.approx(before["entry_price"] / 2)
    assert after["quantity"] == pytest.approx(before["quantity"] * 2)
    assert after["stop_price"] == pytest.approx(75.0)
    assert after["amount"] == before["amount"]          # 원금 불변
    assert after["split_checked_at"]                    # 반영일 기록


def test_second_run_is_idempotent(monkeypatch):
    import aifund
    pos_id = _make_position()
    _fake_yf(monkeypatch, {"APH": pd.Series([2.0], index=[pd.Timestamp("2026-09-03", tz="America/New_York")])})
    aifund.run_split_adjust()
    once = _get(pos_id)
    out2 = aifund.run_split_adjust()                    # 같은 분할을 다시 봐도
    assert out2["adjusted"] == []                       # 재적용 없음
    assert _get(pos_id)["entry_price"] == once["entry_price"]


def test_split_before_entry_ignored(monkeypatch):
    import aifund
    pos_id = _make_position()
    before = _get(pos_id)
    # 진입(08-11) 이전 분할은 이미 진입가에 반영돼 있으므로 건드리면 안 됨
    _fake_yf(monkeypatch, {"APH": pd.Series([2.0], index=[pd.Timestamp("2026-06-10", tz="America/New_York")])})
    out = aifund.run_split_adjust()
    assert out["adjusted"] == []
    assert _get(pos_id)["entry_price"] == before["entry_price"]
