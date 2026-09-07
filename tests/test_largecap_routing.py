"""발굴주 봇이 대형주를 올렸을 때 대형주 계좌로 라우팅 — 회귀 테스트.

계좌는 "누가 찾았나"가 아니라 "어떤 종목이냐"로 정해져야 한다.
실제 사례: AVGO가 발굴주 결재로 올라옴(#55). 같은 종목이 과거엔 발굴주(#6)·
대형주(#14) 양쪽으로 상신됐고, 발굴주 결재였던 #6은 체결이 대형주 계좌로 났다.
"""
import os

import pytest

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db, _conn

init_db()

import aifund


@pytest.fixture
def largecap(monkeypatch):
    """대형주 유니버스를 고정 — 실제 유니버스가 바뀌어도 테스트가 흔들리지 않게."""
    monkeypatch.setattr(aifund, "_largecap_universe", lambda: {"BIGCO", "MEGACO"})
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    yield
    with _conn() as con:
        con.execute("DELETE FROM pending_buy WHERE code IN ('BIGCO','MEGACO','SMALLCO')")
        con.execute("DELETE FROM virtual_positions WHERE code IN ('BIGCO','MEGACO','SMALLCO')")


def _pending(code):
    with _conn() as con:
        con.row_factory = __import__("sqlite3").Row
        r = con.execute(
            "SELECT desk, account, amount FROM pending_buy WHERE code=? AND status='pending'",
            (code,)).fetchone()
    return dict(r) if r else None


def _hold(code, account):
    with _conn() as con:
        con.execute(
            "INSERT INTO virtual_positions (symbol, code, direction, entry_price, quantity, "
            "amount, reasoning, status, opened_at, account, market) "
            "VALUES (?,?,'long',100.0,10,1000.0,'t','open','2026-09-07 00:00:00',?,'US')",
            (code, code, account))


def test_discovery_bot_finding_largecap_routes_to_largecap(largecap):
    """발굴주 경로로 올라온 대형주 → 대형주 계좌 건이 된다."""
    ok = aifund._submit_buy_approval("발굴주", "발굴주", "BIGCO", "BIGCO", 100.0,
                                     2_500_000, ["S"], "US", reason="병목")
    assert ok
    row = _pending("BIGCO")
    assert row["account"] == "대형주" and row["desk"] == "대형주"


def test_routed_buy_uses_largecap_sizing(largecap):
    """금액도 대형주 규칙(시드 10%)으로 바뀐다 — 발굴주 250만이 아니라."""
    aifund._submit_buy_approval("발굴주", "발굴주", "MEGACO", "MEGACO", 100.0,
                                2_500_000, ["S"], "US", reason="x")
    assert _pending("MEGACO")["amount"] == aifund._desk_amount("대형주")


def test_skips_silently_when_already_held_in_largecap(largecap):
    """이미 대형주에 보유 중이면 결재를 아예 안 올린다 — 오너에게 다시 묻지 않는다."""
    _hold("BIGCO", "대형주")
    ok = aifund._submit_buy_approval("발굴주", "발굴주", "BIGCO", "BIGCO", 100.0,
                                     2_500_000, ["S"], "US", reason="병목")
    assert ok is False
    assert _pending("BIGCO") is None


def test_non_largecap_stays_in_discovery(largecap):
    """유니버스 밖 종목은 그대로 발굴주 — 과잉 라우팅 방지."""
    aifund._submit_buy_approval("발굴주", "발굴주", "SMALLCO", "SMALLCO", 100.0,
                                2_500_000, ["S"], "US", reason="무명 상류")
    row = _pending("SMALLCO")
    assert row["account"] == "발굴주" and row["amount"] == 2_500_000


def test_largecap_desk_unaffected(largecap):
    """대형주 데스크가 올린 건은 라우팅 블록을 타지 않는다."""
    aifund._submit_buy_approval("대형주", "대형주", "MEGACO", "MEGACO", 100.0,
                                5_000_000, ["P"], "US", reason="x")
    assert _pending("MEGACO")["account"] == "대형주"


def test_holding_in_other_account_does_not_block(largecap):
    """발굴주에 들고 있어도 대형주 미보유면 올라와야 한다 — 계좌별로 본다."""
    _hold("BIGCO", "발굴주")
    ok = aifund._submit_buy_approval("발굴주", "발굴주", "BIGCO", "BIGCO", 100.0,
                                     2_500_000, ["S"], "US", reason="병목")
    assert ok and _pending("BIGCO")["account"] == "대형주"
