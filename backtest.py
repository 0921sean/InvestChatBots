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
import trading_strategies as ts

# 왕복 비용 (진입, 청산) — 청산에 KR 거래세(~0.18%) 포함
COST = {"US": (0.0005, 0.0005), "KRX": (0.0005, 0.0023)}
MIN_BARS = 300          # 최소 이력(약 1.2년)
_WARMUP = ts.MACD_SLOW + ts.MACD_SIGNAL + 2


# ── 바별 정렬 지표 (길이 n, 워밍업은 None) ───────────────────
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
def backtest_stock(o, strategy, variant, market):
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
    else:
        lb, mb = _bollinger_series(closes)

    in_pos = False
    entry = stop = target = None
    half_done = False

    def _gc(t):
        return ma[t - 1] is not None and ma[t] is not None and ma[t - 1] <= sg[t - 1] and ma[t] > sg[t]

    def _dc(t):
        return ma[t - 1] is not None and ma[t] is not None and ma[t - 1] >= sg[t - 1] and ma[t] < sg[t]

    def _record(e_price, x_price, exit_idx, size=1.0):
        gross = (x_price - e_price) / e_price
        net = gross - ce - cx           # 진입·청산 비용
        trades.append({"ret": net * size, "size": size,
                       "date": dates[exit_idx] if dates else exit_idx})

    for t in range(_WARMUP, n - 1):     # t+1 시가 체결 위해 마지막 봉 제외
        nxt = opens[t + 1]
        if not in_pos:
            fire = False
            if strategy == "momentum":
                fire = _gc(t) and rs[t] is not None and rs[t] >= ts.RSI_MOMENTUM_MIN
            elif variant == "a":        # 볼린저 단순터치
                fire = lb[t] is not None and closes[t] < lb[t]
            else:                        # 볼린저 복귀확인
                fire = (lb[t - 1] is not None and lb[t] is not None
                        and closes[t - 1] < lb[t - 1] and closes[t] >= lb[t])
            if fire:
                in_pos = True
                entry = nxt
                half_done = False
                if strategy == "momentum":
                    stop = min(lows[max(0, t - ts.MOM_STOP_LOOKBACK + 1):t + 1])
                    target = entry + 2.0 * (entry - stop)
        else:
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


# ── 유니버스 실행 + 리포트 ───────────────────────────────────
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_VARIANTS = [("전략1 MACD+RSI", "momentum", "v1", "익절 v1(1:2 전량)"),
             ("전략1 MACD+RSI", "momentum", "v2", "익절 v2(절반+잔량)"),
             ("전략2 볼린저", "meanrev", "a", "(a) 단순터치"),
             ("전략2 볼린저", "meanrev", "b", "(b) 복귀확인")]


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

    result = {}   # (group,variant) -> {full, is, oos}
    for group, strat, var, _label in _VARIANTS:
        acc = {"full": [], "is": [], "oos": []}
        for sym, (o, market) in data.items():
            acc["full"] += backtest_stock(o, strat, var, market)
            half = len(o["close"]) // 2
            acc["is"] += backtest_stock(_slice(o, 0, half), strat, var, market)
            acc["oos"] += backtest_stock(_slice(o, half, len(o["close"])), strat, var, market)
        result[(strat, var)] = {p: aggregate(t) for p, t in acc.items()}
    return counts, result, len(data)


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
