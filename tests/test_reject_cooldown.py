"""오너가 거절한 종목이 곧바로 다시 결재에 오르는 문제 — 회귀 테스트.

실제 사례: HWM이 2026-08-19에 거절됐는데 08-23에 다시 올라와 또 거절됐다.
두 사유는 같은 논지(엔진 단조·재인증 장벽·마진 개선)인데 LLM이 매번 다른 어휘로
써서 텍스트 유사도는 0.19에 불과했다 — 그래서 사유 비교가 아니라 쿨다운으로 막는다.
"""
import os
from datetime import datetime, timedelta

import pytest

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db, _conn, last_rejected_buy

init_db()


def _insert(code, account, status, decided_days_ago=None, reason="사유"):
    decided = None
    if decided_days_ago is not None:
        decided = (datetime.now() - timedelta(days=decided_days_ago)).isoformat(" ", "seconds")
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO pending_buy (ticker, code, desk, account, market, amount, "
            "decision_price, approvers, stock_desc, reason, status, decided_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, code, "S", account, "US", 2_500_000, 100.0, "S", "", reason, status, decided))
        return cur.lastrowid


@pytest.fixture
def clean():
    ids = []
    yield ids
    with _conn() as con:
        for i in ids:
            con.execute("DELETE FROM pending_buy WHERE id=?", (i,))


def test_no_rejection_returns_none(clean):
    clean.append(_insert("NOREJ", "발굴주", "approved", decided_days_ago=1))
    assert last_rejected_buy("NOREJ", "발굴주") is None


def test_finds_rejection_with_age(clean):
    clean.append(_insert("REJ1", "발굴주", "rejected", decided_days_ago=4, reason="엔진 단조 병목"))
    r = last_rejected_buy("REJ1", "발굴주")
    assert r is not None
    assert r["days_ago"] == 4
    assert "엔진 단조" in r["reason"]


def test_rejection_is_scoped_to_account(clean):
    """다른 계좌의 거절은 안 걸린다 — 계좌마다 담당 봇·시드가 다르다."""
    clean.append(_insert("SCOPE", "발굴주", "rejected", decided_days_ago=1))
    assert last_rejected_buy("SCOPE", "발굴주") is not None
    assert last_rejected_buy("SCOPE", "대형주") is None


def test_most_recent_rejection_wins(clean):
    clean.append(_insert("MULTI", "발굴주", "rejected", decided_days_ago=30, reason="옛 사유"))
    clean.append(_insert("MULTI", "발굴주", "rejected", decided_days_ago=2, reason="최근 사유"))
    assert last_rejected_buy("MULTI", "발굴주")["reason"] == "최근 사유"


def test_hwm_case_would_have_been_blocked(clean):
    """실제 사고 재현 — 4일 뒤 재상신은 쿨다운(14일)에 걸려야 한다."""
    from aifund import REJECT_COOLDOWN_DAYS

    clean.append(_insert("HWMX", "발굴주", "rejected", decided_days_ago=4))
    r = last_rejected_buy("HWMX", "발굴주")
    assert r["days_ago"] < REJECT_COOLDOWN_DAYS, "HWM 재상신이 다시 통과한다"


def test_after_cooldown_allowed_again(clean):
    """쿨다운이 지나면 다시 올릴 수 있어야 한다 — 영구 차단이 아니다."""
    from aifund import REJECT_COOLDOWN_DAYS

    clean.append(_insert("OLDREJ", "발굴주", "rejected", decided_days_ago=REJECT_COOLDOWN_DAYS + 1))
    assert last_rejected_buy("OLDREJ", "발굴주")["days_ago"] >= REJECT_COOLDOWN_DAYS
