"""반전봇 — 과매도 반전 T+1 규칙 계좌 (#236, 오너 승인 2026-10-22).

InvestData 연구로 검증된 전략의 라이브 포워드 테스트. LLM 0콜, 순수 규칙.
1차 목적은 수익 과시가 아니라 **거래비용 가정(왕복 30bp)의 실측 검증**:
신호 시점 가격과 가상 체결 기록이 쌓이면 백테스트 가정을 현실과 대조할 수 있다.

전략 (프리즈 — InvestData/quant/STRATEGY.md 와 동일, 임의 변경 금지):
  매일 09:45 ET 스크리닝: 5일 수익률 ≤ -10% AND 개장15분 거래량 ≥ 20일 동구간 평균 7배
  AND 갭 ±40% 이내 AND 주가 ≥ $5 AND 20일 평균 거래대금 ≥ $5M. 보통주만(ETF 제외).
  상위 5개(합성점수) 각 자본 20% 가상 매수 → 다음 거래일 종가 전량 가상 매도. 스톱 없음.

구조 (3단계, 모두 REVERSAL_BOT_ENABLED=true 일 때만):
  1) nightly_screen  07:10 KST — 전일 종가 기준 후보 사전 선별 + 분봉 기준선 수집
                                 (전체 유니버스 스캔은 밤에, 아침엔 후보 수십 개만 조회)
  2) morning_entry   09:46 ET  — 후보들의 개장 15분 분봉 조회 → 조건 확정 → 가상 매수
  3) afternoon_exit  16:05 ET  — 어제(ET) 이전에 연 포지션 전량 종가 청산

안전: 가상 계좌 전용(실거래 없음). 플래그 기본 꺼짐 — 머지해도 라이브 무영향.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db
import toss

logger = logging.getLogger("investchat")

REVERSAL_BOT_ENABLED = os.getenv("REVERSAL_BOT_ENABLED", "false").lower() == "true"
ACCOUNT = "반전봇"

# ── 프리즈 파라미터 (STRATEGY.md) ──
RET5_MAX = -0.10          # 5일 수익률 상한
RVOL_MIN = 7.0            # 개장 15분 상대거래량 하한
GAP_ABS_MAX = 0.40        # 갭 절대값 상한
MIN_PRICE = 5.0           # 달러
MIN_DV20 = 5_000_000      # 20일 평균 거래대금 달러
MAX_POS = 5
POS_FRAC = 0.20           # 자본의 20%/종목
RVOL_BASE_DAYS = 20       # 개장 15분 거래량 기준선 일수

_HERE = os.path.dirname(os.path.abspath(__file__))
SHORTLIST_PATH = os.path.join(_HERE, "reversal_shortlist.json")
UNIVERSE_CSV = os.getenv("REVERSAL_UNIVERSE", os.path.expanduser(
    "~/Desktop/CSB/MyApps/InvestData/cache/universe_core.csv"))
ET = ZoneInfo("America/New_York")


def _load_universe() -> list[str]:
    """InvestData 핵심 유니버스(보통주, ETF 제외 ~2,700). 없으면 빈 리스트."""
    try:
        with open(UNIVERSE_CSV) as f:
            rows = [ln.strip() for ln in f.read().splitlines()[1:] if ln.strip()]
        return rows
    except Exception as e:
        logger.warning(f"반전봇: 유니버스 로드 실패 {e}")
        return []


def _rsi14(closes: list[float]) -> float | None:
    """Wilder RSI(14). trading_strategies 의 구현과 동일 수식."""
    if len(closes) < 15:
        return None
    gains = losses = 0.0
    for i in range(1, 15):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / 14, losses / 14
    for i in range(15, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * 13 + max(ch, 0.0)) / 14
        al = (al * 13 + max(-ch, 0.0)) / 14
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def _et_anchor(date_str: str, hh: int, mm: int) -> str:
    return f"{date_str}T{hh:02d}:{mm:02d}:00-04:00" if _is_edt(date_str) \
        else f"{date_str}T{hh:02d}:{mm:02d}:00-05:00"


def _is_edt(date_str: str) -> bool:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET, hour=12)
    return bool(d.dst())


def _or15_volume(sym: str, date_str: str) -> float | None:
    """해당 거래일 09:31~09:45 봉(15개) 거래량 합. timestamp=봉 종료시각."""
    bars = toss.get_minute_bars(sym, _et_anchor(date_str, 9, 46), count=16)
    vol = 0.0
    seen = 0
    for b in bars:
        ts = b["ts"]
        if date_str not in ts:
            continue
        hhmm = ts[11:16]
        if "09:31" <= hhmm <= "09:45":
            vol += b["volume"]
            seen += 1
    return vol if seen >= 5 else None     # 봉 5개 미만이면 판정 불가


def nightly_screen():
    """07:10 KST (미장 마감 후): 5일 낙폭 후보 선별 + 분봉 기준선 수집 → shortlist 저장."""
    if not REVERSAL_BOT_ENABLED:
        return
    uni = _load_universe()
    if not uni:
        return
    logger.info(f"반전봇 야간 스크리닝 시작: {len(uni)}종목")
    cands = []
    for sym in uni:
        dc = toss.get_daily_candles(sym, count=30)
        if len(dc) < 21:
            continue
        closes = [c["close"] for c in dc]
        px = closes[-1]
        if px < MIN_PRICE:
            continue
        dv20 = sum(c["close"] * c["volume"] for c in dc[-20:]) / 20
        if dv20 < MIN_DV20:
            continue
        if closes[-6] <= 0:
            continue
        ret5 = px / closes[-6] - 1.0
        if ret5 > RET5_MAX:
            continue
        cands.append({"symbol": sym, "ret5": ret5, "prev_close": px,
                      "rsi14": _rsi14(closes[-20:]) or 50.0, "dv20": dv20})
    logger.info(f"반전봇: 낙폭 후보 {len(cands)}개 — 분봉 기준선 수집")

    trade_dates = [c["date"] for c in toss.get_daily_candles("AAPL", count=RVOL_BASE_DAYS + 2)]
    for c in cands:
        vols = []
        for d in trade_dates[-RVOL_BASE_DAYS - 1:-1]:      # 오늘 제외 직전 20거래일
            v = _or15_volume(c["symbol"], d)
            if v is not None and v > 0:
                vols.append(v)
        c["or_vol_base"] = (sum(vols) / len(vols)) if len(vols) >= 10 else None

    shortlist = [c for c in cands if c["or_vol_base"]]
    with open(SHORTLIST_PATH, "w") as fp:
        json.dump({"made_at": datetime.now(ET).isoformat(), "candidates": shortlist}, fp,
                  ensure_ascii=False)
    logger.info(f"반전봇: shortlist {len(shortlist)}개 저장")


def morning_entry():
    """09:46 ET: 후보들의 개장 15분 확인 → 조건 충족 상위 5개 가상 매수."""
    if not REVERSAL_BOT_ENABLED:
        return
    try:
        with open(SHORTLIST_PATH) as fp:
            data = json.load(fp)
    except Exception:
        logger.info("반전봇: shortlist 없음 — 진입 생략")
        return
    today = datetime.now(ET).strftime("%Y-%m-%d")
    made = (data.get("made_at") or "")[:10]
    # shortlist 는 같은 ET 거래일 아침에만 유효 (주말·휴장 이월 방지: 24h 이내)
    try:
        age_h = (datetime.now(ET) - datetime.fromisoformat(data["made_at"])).total_seconds() / 3600
    except Exception:
        age_h = 99
    if age_h > 20:
        logger.info(f"반전봇: shortlist 오래됨({made}) — 진입 생략")
        return

    live = []
    for c in data.get("candidates", []):
        sym = c["symbol"]
        bars = toss.get_minute_bars(sym, _et_anchor(today, 9, 46), count=16)
        tb = [b for b in bars if today in b["ts"] and "09:31" <= b["ts"][11:16] <= "09:45"]
        if len(tb) < 5:
            continue
        day_open = tb[0]["open"]
        or_close = tb[-1]["close"]
        or_vol = sum(b["volume"] for b in tb)
        if or_close < MIN_PRICE or day_open <= 0 or c["prev_close"] <= 0:
            continue
        gap = day_open / c["prev_close"] - 1.0
        rvol = or_vol / c["or_vol_base"]
        if abs(gap) > GAP_ABS_MAX or rvol < RVOL_MIN:
            continue
        live.append({**c, "or_close": or_close, "rvol": rvol, "gap": gap})

    if not live:
        logger.info("반전봇: 오늘 조건 충족 종목 없음")
        return
    # 합성점수 = pct_rank(낙폭) + pct_rank(저RSI) + pct_rank(rvol)  — 후보 내 순위
    def ranks(vals, reverse=False):
        order = sorted(range(len(vals)), key=lambda i: vals[i], reverse=reverse)
        r = [0.0] * len(vals)
        for pos, i in enumerate(order):
            r[i] = pos / max(len(vals) - 1, 1)
        return r
    r1 = ranks([c["ret5"] for c in live])              # 낙폭 클수록(음수 작을수록) 높게
    r2 = ranks([c["rsi14"] for c in live])             # RSI 낮을수록 높게
    r3 = ranks([c["rvol"] for c in live], reverse=False)
    for i, c in enumerate(live):
        c["score"] = (1 - r1[i]) + (1 - r2[i]) + r3[i]
    live.sort(key=lambda c: -c["score"])

    db.ensure_reversal_account()
    pf = db.get_shared_portfolio(ACCOUNT)
    equity = (pf.get("balance") or 0) + (pf.get("invested") or 0)
    n_bought = 0
    for c in live[:MAX_POS]:
        amount = min(equity * POS_FRAC, pf.get("balance", 0) - 1)
        if amount < equity * POS_FRAC * 0.5:            # 자본 부족(전일 포지션 점유) — 생략
            break
        reason = (f"[반전T+1] 5일 {c['ret5']*100:+.1f}%, 개장15분 거래량 {c['rvol']:.1f}배, "
                  f"갭 {c['gap']*100:+.1f}%, RSI {c['rsi14']:.0f} — 투매 소진 반등 가설. "
                  f"내일 종가 전량 청산 예정.")
        pos_id, err = db.buy_shared_position(c["symbol"], c["symbol"], c["or_close"],
                                             amount, reason, market="US", account=ACCOUNT)
        if err:
            logger.info(f"반전봇 매수 실패 {c['symbol']}: {err}")
            continue
        pf = db.get_shared_portfolio(ACCOUNT)
        n_bought += 1
    logger.info(f"반전봇: {n_bought}건 가상 매수 (후보 {len(live)})")


def afternoon_exit():
    """16:05 ET: 오늘(ET) 이전에 연 포지션 전량 종가 청산 — 예외 없음."""
    if not REVERSAL_BOT_ENABLED:
        return
    today_et = datetime.now(ET).strftime("%Y-%m-%d")
    rows = db.get_open_positions(account=ACCOUNT)
    n = 0
    for p in rows:
        opened = (p.get("opened_at") or "")[:19]
        try:   # opened_at 은 UTC(CURRENT_TIMESTAMP) — ET 날짜로 환산
            dt = datetime.strptime(opened, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=ZoneInfo("UTC")).astimezone(ET)
            if dt.strftime("%Y-%m-%d") >= today_et:
                continue                                 # 오늘 산 것은 내일 청산
        except Exception:
            pass
        px = toss.get_price(p["symbol"])
        if not px or px <= 0 or not math.isfinite(px):
            logger.warning(f"반전봇 청산 가격 실패 {p['symbol']} — 다음 장 재시도")
            continue
        pnl, err = db.sell_shared_position(p["id"], px,
                                           "[반전T+1] 익일 종가 규칙 청산 (보유 연장 없음)")
        if not err:
            n += 1
    logger.info(f"반전봇: {n}건 청산")
