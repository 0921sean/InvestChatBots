"""
지수 벤치마크 (명세 §5) — 봇 합산 NAV vs QQQ·SCHD·코스피·코스닥.
같은 baseline(첫 스냅샷) 기준 누적%로 정규화(지수 절대레벨과 수익률을 섞지 않는다).
실데이터만 — 값 없으면 None("데이터 없음"). 하루 1개 스냅샷을 메인 프로세스에서 저장.
"""
import logging

import db

logger = logging.getLogger("investchat.benchmark")

# 벤치마크 지수 (yfinance 심볼)
_INDICES = {"qqq": "QQQ", "spy": "SPY", "schd": "SCHD", "kospi": "^KS11", "kosdaq": "^KQ11"}


def _yf_price(sym: str):
    import yfinance as yf
    try:
        fi = yf.Ticker(sym).fast_info
        p = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
        return float(p) if p else None
    except Exception:
        return None


def _position_price(code: str, market: str):
    """보유 종목 현재가. US=티커 그대로, KR=.KS→.KQ 폴백."""
    us = (market or "").upper() in ("US", "USD")
    for sym in ([code] if us else [f"{code}.KS", f"{code}.KQ"]):
        p = _yf_price(sym)
        if p:
            return p
    return None


def fetch_index_prices() -> dict:
    """{qqq,schd,kospi,kosdaq: 현재가 or None}."""
    return {k: _yf_price(sym) for k, sym in _INDICES.items()}


def bot_combined_nav() -> float:
    """장기+트레이딩 합산 NAV = 두 계좌 현금 + 보유 시가평가(실패 시 원가)."""
    nav = 0.0
    for acct in ("main", "sub"):
        pf = db.get_shared_portfolio(acct)
        nav += pf.get("balance", 0) or 0
    for p in db.get_open_positions():   # 두 계좌 전체
        price = _position_price(p.get("code", ""), p.get("market", "KRX"))
        if price and p.get("quantity"):
            nav += p["quantity"] * price
        else:
            nav += p.get("amount") or 0   # 원가 fallback
    return round(nav, 0)


def capture_snapshot(date: str) -> dict:
    """오늘(date) 스냅샷 저장 — 봇 NAV + 지수. 하루 1회 UPSERT."""
    idx = fetch_index_prices()
    nav = bot_combined_nav()
    db.save_benchmark_snapshot(date, nav, idx["qqq"], idx["schd"], idx["kospi"], idx["kosdaq"], idx.get("spy"))
    logger.info(f"벤치마크 스냅샷 {date}: NAV {nav:,.0f} / {idx}")
    return {"date": date, "bot_nav": nav, **idx}


def _cum(base, latest, key):
    """baseline 대비 누적% (정규화). 데이터 없으면 None."""
    b, l = base.get(key), latest.get(key)
    if not b or l is None:
        return None
    return round((l / b - 1) * 100, 2)


def compute_benchmark() -> dict:
    """봇 vs 지수 누적% (같은 baseline). 스냅샷 없으면 available=False."""
    snaps = db.get_benchmark_snapshots()
    if not snaps:
        return {"available": False}
    base, latest = snaps[0], snaps[-1]
    return {
        "available": True,
        "baseline_date": base["date"], "latest_date": latest["date"],
        "days": len(snaps),
        "bot": _cum(base, latest, "bot_nav"),
        "qqq": _cum(base, latest, "qqq"),
        "spy": _cum(base, latest, "spy"),
        "schd": _cum(base, latest, "schd"),
        "kospi": _cum(base, latest, "kospi"),
        "kosdaq": _cum(base, latest, "kosdaq"),
    }
