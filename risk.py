"""리스크 회로차단기 — 확률적 AI(그리고 수동 승인)조차 뚫을 수 없는 결정론적 방어층.

모든 매수는 execution 직전 precheck_buy()를 통과해야 한다. 게이트/한도는 .env로 조정.
설계 원칙: LLM은 '무엇을 살지' 판단하고, 이 층은 '지금 사도 되는 상태인지'를 규칙으로 강제한다.
  - circuit breaker(절대 차단): kill switch · 일일 손실 한도 · 데이터 이상 → 오너 승인도 무시하고 막음.
  - limit(한도 차단): 포지션 수 · 단일 종목 비중 → 계좌 건전성 상한.
"""
import os
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("investchat")

RISK_GUARD_ENABLED = os.getenv("RISK_GUARD_ENABLED", "").lower() in ("1", "true", "yes")


def _f(env, default):
    try:
        return float(os.getenv(env, "") or default)
    except ValueError:
        return default


# ── 임계값(.env 조정 가능) ──
DAILY_LOSS_PCT = _f("RISK_DAILY_LOSS_PCT", -0.05)     # 계좌 오늘 실현손익 ≤ -5% → 당일 신규매수 중단
MAX_POSITIONS = int(_f("RISK_MAX_POSITIONS", 25))     # 계좌당 동시 보유 상한(집중도 폭주 방지)
MAX_WEIGHT = _f("RISK_MAX_WEIGHT", 0.15)              # 단일 종목 ≤ 계좌 자산의 15%
DATA_FAIL_HALT = _f("RISK_DATA_FAIL_RATE", 0.5)       # 오늘 시세 실패율 > 50% & 실패 10건+ → 데이터 이상 중단


def _today_kst():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


def is_kill_switch_on() -> bool:
    """전역 킬스위치(DB 영속 — 재시작·수동 토글에도 유지)."""
    try:
        from db import get_risk_flag
        return bool(get_risk_flag("kill_switch"))
    except Exception:
        return False


def set_kill_switch(on: bool):
    from db import set_risk_flag
    set_risk_flag("kill_switch", 1 if on else 0)


def daily_realized_pnl(account) -> float:
    """오늘(KST) 청산분 실현손익 합 — 결정론적(시세 조회 없음)."""
    from db import _conn
    with _conn() as con:
        rows = con.execute(
            "SELECT pnl, closed_at FROM virtual_positions "
            "WHERE account=? AND status='closed' AND pnl IS NOT NULL", (account,)).fetchall()
    today = _today_kst()
    tot = 0.0
    for pnl, closed in rows:
        # closed_at은 UTC → KST 변환 후 오늘분만
        try:
            if (datetime.fromisoformat(closed) + timedelta(hours=9)).strftime("%Y-%m-%d") == today:
                tot += pnl or 0
        except Exception:
            continue
    return tot


def _data_anomaly() -> bool:
    """오늘 시세 fetch 실패율이 임계 초과 → 나쁜 데이터로 매매 금지."""
    try:
        from db import get_fetch_health
        today = _today_kst()
        ok = fail = 0
        for r in get_fetch_health(1):
            if r["date"] == today and r["source"] == "price":
                ok, fail = r["ok"], r["fail"]
        return fail >= 10 and fail / max(ok + fail, 1) > DATA_FAIL_HALT
    except Exception:
        return False


def account_state(account) -> dict:
    """리스크 관점 계좌 스냅샷 — 오너 대시보드/판단용."""
    from db import get_shared_portfolio, get_open_positions, ACCT_SEED, DESK_SEED
    pf = get_shared_portfolio(account) or {}
    pos = get_open_positions(account=account)
    cash = pf.get("balance") or 0
    invested = sum((p.get("amount") or 0) for p in pos)
    equity = cash + invested                          # 원가 기준(결정론적)
    seed = ACCT_SEED.get(account, DESK_SEED)
    realized = daily_realized_pnl(account)
    return {
        "account": account, "cash": cash, "invested": invested, "equity": equity,
        "positions": len(pos), "seed": seed,
        "daily_realized": realized, "daily_realized_pct": (realized / seed) if seed else 0,
    }


def precheck_buy(account, code, amount) -> tuple:
    """이 매수를 지금 허용할지 결정론적으로 판정. 반환: (allowed, reason_if_blocked, is_circuit_breaker).
    is_circuit_breaker=True면 '절대 차단'(오너 승인도 무시). False면 한도 차단."""
    if not RISK_GUARD_ENABLED:
        return True, "", False
    try:
        import ops
        if ops.is_safe_mode():
            return False, "🛑 세이프모드 — 인프라 이상으로 매매 자동 중단", True
    except Exception:
        pass
    if is_kill_switch_on():
        return False, "🛑 킬스위치 ON — 전체 매매 수동 중단 상태", True
    if _data_anomaly():
        return False, "🛑 데이터 이상(시세 실패율 급증) — 나쁜 데이터로 매수 금지", True
    st = account_state(account)
    if st["daily_realized_pct"] <= DAILY_LOSS_PCT:
        return False, (f"🛑 일일 손실 한도 도달 ({st['daily_realized_pct']*100:.1f}% ≤ "
                       f"{DAILY_LOSS_PCT*100:.0f}%) — 오늘 신규매수 중단"), True
    if st["positions"] >= MAX_POSITIONS:
        return False, f"⚠️ 포지션 수 상한 ({st['positions']}/{MAX_POSITIONS})", False
    # 단일 종목 비중 상한 — Q지수는 2슬롯(각 50%) 전술 계좌라 설계상 제외.
    equity = st["equity"] or amount
    if account != "Q지수" and equity and (amount / equity) > MAX_WEIGHT:
        return False, f"⚠️ 단일 종목 비중 상한 초과 ({amount/equity*100:.0f}% > {MAX_WEIGHT*100:.0f}%)", False
    return True, "", False


def snapshot() -> dict:
    """오너 리스크 대시보드 — 브레이커 상태 + 계좌별 스냅샷."""
    from db import DESK_ACCOUNTS
    accts = [account_state(a) for a in DESK_ACCOUNTS]
    breakers = {
        "kill_switch": is_kill_switch_on(),
        "data_anomaly": _data_anomaly(),
        "daily_loss_halted": [a["account"] for a in accts if a["daily_realized_pct"] <= DAILY_LOSS_PCT],
    }
    return {"enabled": RISK_GUARD_ENABLED, "breakers": breakers, "accounts": accts,
            "thresholds": {"daily_loss_pct": DAILY_LOSS_PCT, "max_positions": MAX_POSITIONS,
                           "max_weight": MAX_WEIGHT}}
