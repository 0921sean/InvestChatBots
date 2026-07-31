"""
백테스트 하네스 (명세 §6-A) — 라이브와 별도·병행. trading_strategies 지표 재사용.

무결성:
- 미래참조 금지: 바 t의 신호는 closes[:t+1](그 시점까지)로만 판정, 체결은 t+1 시가(다음 봉 시가).
- 비용: 진입/청산 각 수수료+슬리피지, KR 청산엔 거래세 포함(왕복 KR ~0.28% / US ~0.10%).
- 아웃오브샘플: 기간을 전/후반(IS/OOS)로 나눠 각각 지표 보고(전략은 파라미터 피팅 없음).
- 생존편향: 유니버스=현재 지수 구성종목 → 상폐 미포함. 무료 소스 한계로 리포트에 명시.
- 최소봉수 미달·데이터 없음 종목은 스킵(카운트).

비교(명세):
- 전략1 MACD+RSI: 익절 v1(1:2 전량) vs v2(절반+잔량).
- 전략2 볼린저: (a)단순터치 vs (b)복귀확인.

지표: 승률·손익비·거래당 기댓값·누적수익·MDD·거래횟수.
"""
import logging

import trading_strategies as ts

try:                                       # M 튜닝값은 비공개(strategy_private)
    from strategy_private import MINERVINI_STOP_PCT, MINERVINI_BREAKOUT
except ImportError:
    from strategy_private_example import MINERVINI_STOP_PCT, MINERVINI_BREAKOUT

logger = logging.getLogger("investchat.backtest")

# 왕복 비용 (진입, 청산) — 청산에 KR 거래세(~0.18%) 포함
COST = {"US": (0.0005, 0.0005), "KRX": (0.0005, 0.0023)}
MIN_BARS = 300          # 최소 이력(약 1.2년)
_WARMUP = ts.MACD_SLOW + ts.MACD_SIGNAL + 2


# ── 바별 정렬 지표 (길이 n, 워밍업은 None) ───────────────────
def _sma_series(closes, period):
    """단순이동평균 시계열(길이 n, period 미만은 None). O(n) 롤링."""
    n = len(closes)
    out = [None] * n
    if n < period:
        return out
    s = sum(closes[:period])
    out[period - 1] = s / period
    for i in range(period, n):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def _sma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def minervini_entry(closes, in_uptrend):
    """M 진입 신호(최신 바 기준 — 라이브 Q용). in_uptrend=시장필터(SPY>200MA).
    트렌드템플릿(정배열+200선 상승+52주 저점 +30%/고점 25%이내) + N일 신고가 돌파.
    backtest_stock의 _mini_fire와 동일 규칙."""
    if not in_uptrend or len(closes) < 252:
        return False
    c = closes[-1]
    s50, s150, s200 = _sma(closes, 50), _sma(closes, 150), _sma(closes, 200)
    s200_prev = _sma(closes[:-21], 200)
    if None in (s50, s150, s200, s200_prev):
        return False
    lo, hi = min(closes[-252:]), max(closes[-252:])
    if not (c > s50 > s150 > s200 and s200 > s200_prev and c >= 1.30 * lo and c >= 0.75 * hi):
        return False
    return c >= max(closes[-MINERVINI_BREAKOUT:])


def minervini_exit(closes):
    """M 청산(라이브): 종가가 50일선 하회(추세 이탈)."""
    s50 = _sma(closes, 50)
    return s50 is not None and closes[-1] < s50


def _aligned_macd(closes):
    macd_line, signal = ts.macd_series(closes)
    n = len(closes)
    ma = [None] * n
    sg = [None] * n
    for i in range(len(signal)):
        ma[n - len(signal) + i] = macd_line[-len(signal) + i]
        sg[n - len(signal) + i] = signal[i]
    return ma, sg


def _aligned_rsi(closes):
    rs = ts.rsi_series(closes)
    n = len(closes)
    out = [None] * n
    for i in range(len(rs)):
        out[n - len(rs) + i] = rs[i]
    return out


def _bollinger_series(closes, period=ts.BB_PERIOD, std=ts.BB_STD):
    n = len(closes)
    lower = [None] * n
    mid = [None] * n
    for i in range(period - 1, n):
        w = closes[i - period + 1:i + 1]
        m = sum(w) / period
        sd = (sum((x - m) ** 2 for x in w) / period) ** 0.5
        mid[i] = m
        lower[i] = m - std * sd
    return lower, mid


# ── 종목 1개 백테스트 → 체결 시각(exit_idx)·순수익률 트레이드 목록 ──
def backtest_stock(o, strategy, variant, market, market_ok=None, hard_stop_pct=None):
    """market_ok: 시장 환경 필터(진입 허용 날짜 set). M에만 적용 — 약세장(SPY<200MA) 진입 차단.
    hard_stop_pct: 전 전략 공통 하드스탑(예 -0.15). 진입가 대비 이 이하로 떨어지면 다음 봉 시가 청산.
    None이면 필터·하드스탑 없음(기존 동작)."""
    closes, opens, lows = o["close"], o["open"], o["low"]
    dates = o.get("dates")
    n = len(closes)
    if n < MIN_BARS:
        return []
    ce, cx = COST[market]
    trades = []

    if strategy == "momentum":
        ma, sg = _aligned_macd(closes)
        rs = _aligned_rsi(closes)
    elif strategy == "minervini":
        s50 = _sma_series(closes, 50)
        s150 = _sma_series(closes, 150)
        s200 = _sma_series(closes, 200)
    else:
        lb, mb = _bollinger_series(closes)

    in_pos = False
    entry = stop = target = entry_idx = None
    half_done = False

    def _gc(t):
        return ma[t - 1] is not None and ma[t] is not None and ma[t - 1] <= sg[t - 1] and ma[t] > sg[t]

    def _dc(t):
        return ma[t - 1] is not None and ma[t] is not None and ma[t - 1] >= sg[t - 1] and ma[t] < sg[t]

    def _mini_fire(t):
        """미너비니 진입: 트렌드템플릿(정배열+200선 상승+52주 저점대비 +30%·고점 25%이내) + N일 신고가 돌파.
        (단일종목판 — 상대강도 RS는 교차종목 데이터라 생략.)"""
        if t < 252 or s50[t] is None or s150[t] is None or s200[t] is None or s200[t - 21] is None:
            return False
        c = closes[t]
        lo = min(closes[t - 251:t + 1])
        hi = max(closes[t - 251:t + 1])
        if not (c > s50[t] > s150[t] > s200[t] and s200[t] > s200[t - 21]
                and c >= 1.30 * lo and c >= 0.75 * hi):
            return False
        return c >= max(closes[t - MINERVINI_BREAKOUT + 1:t + 1])   # 신고가 돌파(피봇 프록시)

    def _record(e_price, x_price, exit_idx, size=1.0):
        gross = (x_price - e_price) / e_price
        net = gross - ce - cx           # 진입·청산 비용
        trades.append({"ret": net * size, "size": size,
                       "date": dates[exit_idx] if dates else exit_idx,
                       "entry_date": dates[entry_idx] if dates else entry_idx})  # regime 분해용

    for t in range(_WARMUP, n - 1):     # t+1 시가 체결 위해 마지막 봉 제외
        nxt = opens[t + 1]
        if not in_pos:
            fire = False
            if strategy == "momentum":
                fire = _gc(t) and rs[t] is not None and rs[t] >= ts.RSI_MOMENTUM_MIN
            elif strategy == "minervini":
                fire = _mini_fire(t) and (market_ok is None or dates is None
                                          or dates[t + 1] in market_ok)   # 시장 환경 필터
            elif variant == "a":        # 볼린저 단순터치
                fire = lb[t] is not None and closes[t] < lb[t]
            else:                        # 볼린저 복귀확인
                fire = (lb[t - 1] is not None and lb[t] is not None
                        and closes[t - 1] < lb[t - 1] and closes[t] >= lb[t])
            if fire:
                in_pos = True
                entry = nxt
                entry_idx = t + 1        # 체결 봉(= 진입일, regime 분해 기준)
                half_done = False
                if strategy == "momentum":
                    stop = min(lows[max(0, t - ts.MOM_STOP_LOOKBACK + 1):t + 1])
                    target = entry + 2.0 * (entry - stop)
                elif strategy == "minervini":
                    stop = entry * (1 + MINERVINI_STOP_PCT)   # 타이트 스탑
        else:
            if hard_stop_pct is not None and closes[t] <= entry * (1 + hard_stop_pct):
                _record(entry, nxt, t + 1, size=(0.5 if half_done else 1.0))   # 공통 하드스탑
                in_pos = False
                continue
            if strategy == "momentum":
                hit_stop = closes[t] <= stop
                hit_tgt = closes[t] >= target
                trend_out = _dc(t) or (rs[t] is not None and rs[t] < 50)
                if variant == "v1":     # 전량 청산
                    if hit_stop or hit_tgt or trend_out:
                        _record(entry, nxt, t + 1)
                        in_pos = False
                else:                    # v2: 목표 도달 시 절반, 잔량 조건부
                    if not half_done and hit_tgt:
                        _record(entry, nxt, t + 1, size=0.5)
                        half_done = True
                    elif hit_stop or trend_out or (half_done and closes[t] <= entry):
                        _record(entry, nxt, t + 1, size=0.5 if half_done else 1.0)
                        in_pos = False
            elif strategy == "minervini":   # 방어적 청산: 50일선 하회(추세 이탈) or 타이트 스탑
                if (s50[t] is not None and closes[t] < s50[t]) or closes[t] <= stop:
                    _record(entry, nxt, t + 1)
                    in_pos = False
            else:                        # 볼린저 청산: 중심선 익절 or 하단 재이탈 손절
                if mb[t] is not None and (closes[t] >= mb[t] or closes[t] < lb[t]):
                    _record(entry, nxt, t + 1)
                    in_pos = False
    return trades


# ── 집계 ─────────────────────────────────────────────────────
def aggregate(trades):
    if not trades:
        return {"n": 0}
    rets = [x["ret"] for x in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    # 총 P&L·MDD: 청산일 정렬 가법 자본곡선(1단위/트레이드, %p 누적) — 독립 트레이드라 복리 아님
    ordered = sorted(trades, key=lambda x: str(x.get("date", "")))
    eq = peak = mdd = 0.0
    for x in ordered:
        eq += x["ret"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {
        "n": len(trades),
        "win_rate": round(len(wins) / len(rets) * 100, 1),
        "payoff": round(avg_w / abs(avg_l), 2) if avg_l else None,   # 손익비
        "expectancy": round(sum(rets) / len(rets) * 100, 3),         # 거래당 기댓값 %
        "total_pnl": round(eq * 100, 1),                             # 총 순P&L(합, %p·1단위/거래)
        "mdd": round(mdd * 100, 1),                                  # 최대낙폭(%p, 가법 자본곡선)
    }


# ── 시장 상황(regime) 분류·분해 ──────────────────────────────
def classify_regime(bench_closes, lookback=63, up=0.05, down=-0.05):
    """벤치마크 종가 → 각 인덱스 장세 라벨. 최근 lookback봉(≈3개월) 수익률로
    ≥up '상승' / ≤down '하락' / 그 외 '횡보'. 초기(<lookback)는 '미정'."""
    out = []
    for i in range(len(bench_closes)):
        if i < lookback or bench_closes[i - lookback] == 0:
            out.append("미정")
        else:
            r = bench_closes[i] / bench_closes[i - lookback] - 1
            out.append("상승" if r >= up else "하락" if r <= down else "횡보")
    return out


def market_uptrend_dates(bench, ma=200):
    """벤치마크가 상승국면(종가 > ma일선)인 날짜 set — M 시장 환경 필터용.
    bench: {'close','dates'} (_fetch_bench 형식). 미너비니 '약세장 매수 금지' 원칙."""
    closes, dates = bench["close"], bench["dates"]
    sma = _sma_series(closes, ma)
    return {dates[i] for i in range(len(closes)) if sma[i] is not None and closes[i] > sma[i]}


def segment_by_regime(trades, regime_by_date):
    """거래를 '진입일의 장세'로 분류 → 장세별 aggregate. regime_by_date: {date: 라벨}.
    이게 이 하네스의 핵심 — '어느 장세에서 강한가'를 본다."""
    buckets = {}
    for t in trades:
        lab = regime_by_date.get(str(t.get("entry_date", "")), "미정")
        buckets.setdefault(lab, []).append(t)
    return {lab: aggregate(ts) for lab, ts in buckets.items()}


# ── 유니버스 실행 + 리포트 ───────────────────────────────────
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_VARIANTS = [("전략1 MACD+RSI", "momentum", "v1", "익절 v1(1:2 전량)"),
             ("전략1 MACD+RSI", "momentum", "v2", "익절 v2(절반+잔량)"),
             ("전략2 볼린저", "meanrev", "a", "(a) 단순터치"),
             ("전략2 볼린저", "meanrev", "b", "(b) 복귀확인"),
             ("전략3 미너비니", "minervini", "std", "트렌드템플릿+돌파")]


def _load_universe(market):
    fn = "universe_us.txt" if market == "US" else "universe_kr.txt"
    try:
        with open(os.path.join(_DIR, fn)) as f:
            return [ln.strip().split("|")[0] for ln in f if ln.strip()]
    except Exception:
        return []


def _fetch(symbols, period="6y", chunk=40):
    """{yfsym: {'open','low','close'} 정렬 배열}."""
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        try:
            df = yf.download(batch, period=period, interval="1d", auto_adjust=True,
                             group_by="ticker", progress=False, threads=True)
        except Exception:
            continue
        for s in batch:
            try:
                sub = df[s] if len(batch) > 1 else df
                sub = sub.dropna(subset=["Open", "Low", "Close"])
                if len(sub) >= MIN_BARS:
                    out[s] = {"open": [float(x) for x in sub["Open"]],
                              "low": [float(x) for x in sub["Low"]],
                              "close": [float(x) for x in sub["Close"]],
                              "dates": [str(d.date()) for d in sub.index]}
            except Exception:
                continue
    return out


def _fetch_bench(sym, period="6y"):
    """벤치마크(단일 티커) 일봉 — regime용. _fetch는 다종목 배치라 단일에 취약해서 별도."""
    import yfinance as yf
    df = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)
    return {"close": [float(x) for x in df["Close"]],
            "dates": [str(d.date()) for d in df.index]}


def _slice(o, a, b):
    return {k: v[a:b] for k, v in o.items()}


def run(period="6y"):
    """전 유니버스 백테스트 → 변형별(전체/IS/OOS) 트레이드 집계."""
    data = {}
    counts = {"US": [0, 0], "KRX": [0, 0]}   # [유니버스, 조회성공]
    for market in ("US", "KRX"):
        uni = _load_universe(market)
        counts[market][0] = len(uni)
        fetched = _fetch(uni, period)
        counts[market][1] = len(fetched)
        for sym, o in fetched.items():
            data[sym] = (o, market)

    # 시장 상황(regime) — US 벤치마크 SPY 기준(진입일 장세로 거래 분해)
    try:
        b = _fetch_bench("SPY", period)
        regime_by_date = dict(zip(b["dates"], classify_regime(b["close"])))
    except Exception as e:
        logger.warning(f"SPY regime fetch 실패: {e}")
        regime_by_date = {}

    result = {}   # (group,variant) -> {full, is, oos, regime}
    for group, strat, var, _label in _VARIANTS:
        acc = {"full": [], "is": [], "oos": []}
        for sym, (o, market) in data.items():
            acc["full"] += backtest_stock(o, strat, var, market)
            half = len(o["close"]) // 2
            acc["is"] += backtest_stock(_slice(o, 0, half), strat, var, market)
            acc["oos"] += backtest_stock(_slice(o, half, len(o["close"])), strat, var, market)
        rd = {p: aggregate(t) for p, t in acc.items()}
        rd["regime"] = segment_by_regime(acc["full"], regime_by_date)   # 장세별 분해
        result[(strat, var)] = rd
    return counts, result, len(data)


# ── Q = B+M 블렌드 (자본 인지 포트폴리오) ────────────────────
# Q의 정체: M(미너비니, 시장필터) + B(볼린저 복귀확인)를 한 계좌에 넣고 M 우선 배정.
# M이 선별적이라 비는 자본을 B가 채움 → 상승장 고엣지(M) + 전천후(B) 결합.
# (strategy, variant, use_market_filter, priority) — priority 낮을수록 슬롯 우선
Q_SPEC = [("minervini", "std", True, 0),
          ("meanrev", "b", False, 1)]


def collect_blend_trades(data, spec, market_ok, hard_stop_pct=None, market="US"):
    """spec의 각 구성 전략을 전 종목에 돌려 (진입일·청산일·순수익·우선순위) 거래 목록으로 모음."""
    trades = []
    for strategy, variant, use_filter, prio in spec:
        mo = market_ok if use_filter else None
        for o in data.values():
            try:
                for t in backtest_stock(o, strategy, variant, market,
                                        market_ok=mo, hard_stop_pct=hard_stop_pct):
                    if t.get("entry_date") and t.get("date") and t["date"] > t["entry_date"]:
                        trades.append({"e": t["entry_date"], "x": t["date"],
                                       "ret": t["ret"], "prio": prio})
            except Exception as e:
                logger.debug(f"블렌드 거래수집 실패: {e}")
    return trades


def portfolio_sim(trades, calendar, init=1.0, weight=0.02, max_pos=50):
    """자본 인지 계좌 시뮬(순수 — 네트워크 X). 자리·현금 제약 하 진입, 매도 시 자본 회수.
    trades: [{e(진입일),x(청산일),ret(순수익),prio}]. calendar: 거래일 리스트.
    보유는 원가평가(실현 기준 곡선 — 최종수익 정확, 중간 보수적).
    반환: (curve[(date,equity)], {final_return, mdd, avg_positions})."""
    from collections import defaultdict
    ent = defaultdict(list)
    for t in trades:
        ent[t["e"]].append(t)
    dates = sorted(set(calendar) | {t["e"] for t in trades} | {t["x"] for t in trades})
    cash, opens, closing = init, [], defaultdict(list)
    curve, pos_counts, peak, mdd = [], [], init, 0.0
    for d in dates:
        for p in closing.get(d, []):
            cash += p["amt"] * (1 + p["ret"])
            opens.remove(p)
        for t in sorted(ent.get(d, []), key=lambda x: x.get("prio", 0)):
            if len(opens) >= max_pos:
                break
            amt = (cash + sum(p["amt"] for p in opens)) * weight
            if amt > 0 and cash >= amt:
                cash -= amt
                p = {"amt": amt, "ret": t["ret"]}
                opens.append(p)
                closing[t["x"]].append(p)
        eq = cash + sum(p["amt"] for p in opens)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        curve.append((d, eq))
        pos_counts.append(len(opens))
    stats = {"final_return": (curve[-1][1] / init - 1) if curve else 0.0, "mdd": mdd,
             "avg_positions": sum(pos_counts) / len(pos_counts) if pos_counts else 0.0}
    return curve, stats


def _row(m):
    if not m or m.get("n", 0) == 0:
        return "| 0 | — | — | — | — | — |"
    return (f"| {m['n']} | {m['win_rate']}% | {m['payoff']} | "
            f"{m['expectancy']}% | {m['total_pnl']}%p | {m['mdd']}%p |")


def write_report(date_str, path=None):
    counts, result, n_stocks = run()
    path = path or os.path.join(_DIR, "docs", "backtest_report.md")
    L = [f"# ICB 백테스트 리포트 — {date_str}", ""]
    L.append(f"- 유니버스: US {counts['US'][1]}/{counts['US'][0]} · "
             f"KR {counts['KRX'][1]}/{counts['KRX'][0]} 종목 (최소 {MIN_BARS}봉 이상, 총 {n_stocks})")
    L.append(f"- 체결: 신호 다음 봉 시가 · 비용 왕복 US {sum(COST['US'])*100:.2f}% / "
             f"KR {sum(COST['KRX'])*100:.2f}%(거래세 포함)")
    L.append("- 장세(regime): **SPY 최근 63거래일(~3개월) 수익률** ≥+5% 상승 / ≤−5% 하락 / 그 외 횡보. "
             "거래는 **진입일 장세**로 분류.")
    L.append("- ⚠️ 한계: 유니버스=**현재 지수 구성종목**이라 상폐·편출 미포함 → **생존편향**(무료 소스 한계). "
             "일봉 granularity(장중 미모델). 파라미터 피팅 없음(규칙 고정).")
    L.append("")
    cur_group = None
    for group, strat, var, label in _VARIANTS:
        if group != cur_group:
            L += ["", f"## {group}", ""]
            cur_group = group
        r = result[(strat, var)]
        L.append(f"### {label}")
        L.append("| 기간 | 거래 | 승률 | 손익비 | 거래당기댓값 | 총순P&L(합·1단위) | MDD |")
        L.append("|---|---|---|---|---|---|---|")
        for p, plabel in (("full", "전체"), ("is", "IS(전반)"), ("oos", "OOS(후반)")):
            L.append(f"| {plabel} " + _row(r[p]))
        L.append("")
        # 장세별 분해 (진입일 SPY 기준) — "어느 장세에서 강한가"
        reg = r.get("regime", {})
        if reg:
            L.append("_장세별 (진입일 SPY 기준)_")
            L.append("| 장세 | 거래 | 승률 | 손익비 | 거래당기댓값 | 총순P&L(합·1단위) | MDD |")
            L.append("|---|---|---|---|---|---|---|")
            for lab in ("상승", "횡보", "하락", "미정"):
                if lab in reg:
                    L.append(f"| {lab} " + _row(reg[lab]))
            L.append("")
    # 검증 판정: 전체+OOS 기댓값>0
    L.append("## 검증 판정 (라이브 반영 권고)")
    for group, strat, var, label in _VARIANTS:
        r = result[(strat, var)]
        ok = (r["full"].get("expectancy", -1) or -1) > 0 and (r["oos"].get("expectancy", -1) or -1) > 0
        L.append(f"- {group} {label}: **{'통과' if ok else '미통과'}** "
                 f"(전체 기댓값 {r['full'].get('expectancy')}% / OOS {r['oos'].get('expectancy')}%)")
    L.append("\n> 통과분만 라이브 반영 권고. 실제 반영은 승인 후.")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
    return path, counts, result
