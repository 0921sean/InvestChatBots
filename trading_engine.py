"""
트레이딩 계좌(account='sub') 규칙엔진 실행기 — 명세 §2. LLM 없이 유니버스 스캔 → 규칙 매수/매도.
서브 사이클(12시 KR / 24시 US)에서 호출. 라이브 체결 = 실행 시점 현재가(§4).

  · 매수: 유니버스 각 종목 스캔 → 차트천재(모멘텀)·역추세봇(볼린저) 신호 발화 시 매수(독립 병렬).
  · 매도: 보유 sub 포지션을 '연 전략' 청산규칙으로만 + -20% 하드스톱(should_exit).
  · 비중/상한: position_amount / MAX_POSITIONS (§4).

토큰 비용 없음(가격만 조회). dry_run=True면 스캔·로그만, 매매 안 함.
"""
import logging
import os

import trading_strategies as ts
from db import (buy_shared_position, sell_shared_position, get_open_positions,
                get_shared_portfolio, create_round, complete_round, save_message, _conn)
from notifier import notify

logger = logging.getLogger("investchat.trading")
_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNT = "sub"
_BOT = {ts.MOMENTUM: "차트천재", ts.MEANREV: "역추세봇"}


def _norm(market) -> str:
    return "US" if (market or "").upper() in ("US", "USD") else "KRX"


def load_universe(market) -> list[str]:
    fn = "universe_us.txt" if _norm(market) == "US" else "universe_kr.txt"
    try:
        with open(os.path.join(_DIR, fn)) as f:
            return [ln.strip() for ln in f if ln.strip()]
    except Exception as e:
        logger.warning(f"유니버스 로드 실패({fn}): {e}")
        return []


def _kr_map() -> dict:
    """6자리 코드 → yfinance 심볼(.KS/.KQ) — 보유 포지션 청산 시 심볼 복원용."""
    return {s.split(".")[0]: s for s in load_universe("KRX")}


def _fetch_ohlc(symbols: list[str], chunk: int = 50) -> dict:
    """yfinance 배치 다운로드 → {yfsym: (closes, lows, price)}. 지표에 충분한 봉만."""
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        try:
            df = yf.download(batch, period="4mo", interval="1d", group_by="ticker",
                             auto_adjust=True, progress=False, threads=True)
        except Exception as e:
            logger.warning(f"OHLC 배치 실패({batch[0]}…): {e}")
            continue
        for s in batch:
            try:
                sub = df[s] if len(batch) > 1 else df
                closes = [float(x) for x in sub["Close"].dropna().tolist()]
                lows = [float(x) for x in sub["Low"].dropna().tolist()]
                if len(closes) >= ts._MIN_BARS:
                    out[s] = (closes, lows, closes[-1])
            except Exception:
                continue
    return out


def fetch_one(code: str, market):
    """단일 종목 일봉 (closes, lows, price) 또는 None. KR은 .KS→.KQ 폴백."""
    if not code:
        return None
    cands = [code] if _norm(market) == "US" else [f"{code}.KS", f"{code}.KQ"]
    for sym in cands:
        d = _fetch_ohlc([sym])
        if sym in d:
            return d[sym]
    return None


def _log(round_id, content):
    save_message(round_id, "System", "", content)


# ── 매도(청산) — 연 전략 규칙 + 하드스톱 ─────────────────────
def _run_exits(market, dry_run, round_id) -> list:
    mkt = _norm(market)
    positions = get_open_positions(market=mkt, account=ACCOUNT)
    if not positions:
        return []
    kr_map = _kr_map() if mkt == "KRX" else {}
    sold = []
    for p in positions:
        code = p["code"] or ""
        yfsym = code if mkt == "US" else kr_map.get(code, f"{code}.KS")
        data = _fetch_ohlc([yfsym])
        if yfsym not in data:
            continue
        closes, _lows, price = data[yfsym]
        entry = p["entry_price"] or 0
        pnl_pct = ((price - entry) / entry * 100) if entry else 0.0
        do_exit, reason = ts.should_exit(p["strategy"] or "", closes, pnl_pct,
                                         price=price, stop=p["stop_price"], entry=entry)
        if not do_exit:
            continue
        tag = _BOT.get(p["strategy"], "규칙")
        if dry_run:
            sold.append(f"[DRY] {p['symbol']} 청산 예정 ({reason}, {pnl_pct:+.1f}%)")
            continue
        pnl, err = sell_shared_position(p["id"], price, f"{tag}: {reason}")
        if err:
            logger.warning(f"청산 실패 {p['symbol']}: {err}")
            continue
        sold.append(f"{p['symbol']} 청산 · {reason} · 손익 {pnl:+,.0f}원")
    if sold:
        _log(round_id, "🔻 트레이딩 청산\n" + "\n".join(f"• {s}" for s in sold))
    return sold


# ── 매수(진입) — 유니버스 스캔, 독립 병렬 신호 ───────────────
def _run_buys(market, dry_run, round_id) -> list:
    mkt = _norm(market)
    open_positions = get_open_positions(market=mkt, account=ACCOUNT)
    held = {p["code"] for p in open_positions}
    open_count = len(get_open_positions(account=ACCOUNT))   # 계좌 전체 동시 보유수
    universe = [s for s in load_universe(market) if s.split(".")[0] not in held]
    if not universe:
        return []

    capital = get_shared_portfolio(ACCOUNT).get("balance", 0)
    amount = ts.position_amount(ts.TRADING_BALANCE)
    ohlc = _fetch_ohlc(universe)
    bought = []
    for yfsym, (closes, lows, price) in ohlc.items():
        if not ts.can_open(open_count):
            break
        fired = ts.scan_entries(closes)
        if not fired:
            continue
        strat = ts.MOMENTUM if ts.MOMENTUM in fired else ts.MEANREV   # 겹치면 모멘텀 우선
        code = yfsym.split(".")[0] if mkt == "KRX" else yfsym
        stop = ts.momentum_stop(lows) if strat == ts.MOMENTUM else None
        tag = _BOT[strat]
        if dry_run:
            bought.append(f"[DRY] {code} 매수신호 · {tag} · ${price:,.2f}"
                          + (f" (손절 {stop:,.2f})" if stop else ""))
            open_count += 1
            continue
        if capital < amount:
            break
        pos_id, err = buy_shared_position(code, code, price, amount,
                                          f"{tag}: 규칙 신호 진입", mkt, ACCOUNT)
        if err:
            logger.info(f"매수 skip {code}: {err}")
            continue
        with _conn() as con:      # 전략 태그 + 손절가 저장
            con.execute("UPDATE virtual_positions SET strategy=?, stop_price=? WHERE id=?",
                        (strat, stop, pos_id))
        capital -= amount
        open_count += 1
        bought.append(f"{code} 매수 · {tag} · 진입 {price:,.2f}"
                      + (f" · 손절 {stop:,.2f}" if stop else ""))
    if bought:
        _log(round_id, "🟢 트레이딩 진입\n" + "\n".join(f"• {b}" for b in bought))
    return bought


def run_trading_cycle(market, dry_run: bool = False):
    """서브 사이클 진입점: 청산 먼저, 그 다음 진입 스캔."""
    mkt = _norm(market)
    label = "미국" if mkt == "US" else "한국"
    prefix = "[DRY] " if dry_run else ""
    round_id = create_round(f"{prefix}{label} 트레이딩 규칙엔진 스캔")
    _log(round_id, f"⚙️ {label} 트레이딩 계좌 규칙엔진 실행 "
                   f"(차트천재=모멘텀 / 역추세봇=볼린저, 유니버스 {len(load_universe(market))}종목)")
    try:
        sold = _run_exits(market, dry_run, round_id)
        bought = _run_buys(market, dry_run, round_id)
        if not sold and not bought:
            _log(round_id, "신호 없음 — 청산·진입 모두 해당 없음")
    finally:
        complete_round(round_id)
    if not dry_run and (sold or bought):
        notify(f"⚙️ {label} 트레이딩 규칙엔진",
               f"진입 {len(bought)} · 청산 {len(sold)}", priority="default", cooldown=0)
    return {"sold": sold, "bought": bought}
