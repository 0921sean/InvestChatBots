"""Q(룰봇) 전략 정밀 재검증 — InvestData 로컬 분봉(4.7년) 기반.

기존 백테스트는 yfinance 일봉이라 (a)리밋에 막히고 (b)장중 정보가 없어 손절 체결·갭을
검증하지 못했다. 로컬 아카이브로 같은 전략을 다시 돌려 파라미터를 데이터로 튜닝한다.

사용:  venv/bin/python backtest_minute.py --limit 300
"""
import argparse
import statistics
import sys

import backtest as bt
import minute_data as md


def _sharpe(rets):
    if len(rets) < 2:
        return 0.0
    sd = statistics.pstdev(rets)
    return (statistics.fmean(rets) / sd) if sd else 0.0


def _mdd(equity):
    peak, mdd = equity[0], 0.0
    for x in equity:
        peak = max(peak, x)
        if peak:
            mdd = min(mdd, x / peak - 1)
    return mdd


def _equity_curve(trades, init=1.0, weight=0.02):
    """트레이드를 시간순으로 누적한 단순 자본곡선(비중 고정)."""
    eq, cur = [init], init
    for t in sorted(trades, key=lambda x: x.get("exit_date") or ""):
        cur *= (1 + weight * t["ret"])
        eq.append(cur)
    return eq


def run(symbols, strategies, market="US", bench_sym="SPY", verbose=True):
    """전략별 백테스트 → 요약 지표. 반환 {strategy: stats}."""
    data = md.load_many_daily(symbols)
    if verbose:
        print(f"로드: {len(data)}/{len(symbols)}종목 (일봉 260일+ 보유분)", flush=True)
    if not data:
        return {}
    # 시장 필터(SPY 200MA 위 날짜) — trend 전략에만 적용
    market_ok = None
    bench = md.load_daily(bench_sym)
    if bench and len(bench["close"]) > 200:
        market_ok = bt.market_uptrend_dates({"close": bench["close"], "dates": bench["dates"]})
        if verbose:
            print(f"시장필터: {bench_sym} {len(market_ok)}일 상승국면", flush=True)

    results = {}
    for name, strategy, variant in strategies:
        all_trades = []
        for sym, o in data.items():
            try:
                tr = bt.backtest_stock(o, strategy, variant, market,
                                       market_ok=market_ok if strategy == "trend" else None)
                for t in tr:
                    t["symbol"] = sym
                all_trades.extend(tr)
            except Exception:
                continue
        if not all_trades:
            results[name] = {"trades": 0}
            continue
        rets = [t["ret"] for t in all_trades]
        wins = [r for r in rets if r > 0]
        eq = _equity_curve(all_trades)
        results[name] = {
            "trades": len(rets),
            "win_rate": len(wins) / len(rets),
            "avg": statistics.fmean(rets),
            "median": statistics.median(rets),
            "best": max(rets), "worst": min(rets),
            "expectancy": statistics.fmean(rets),
            "sharpe_per_trade": _sharpe(rets),
            "total_return": eq[-1] - 1,
            "mdd": _mdd(eq),
            "top3_share": (sum(sorted(rets, reverse=True)[:3]) / sum(rets)) if sum(rets) else 0,
        }
        if verbose:
            s = results[name]
            print(f"\n■ {name}", flush=True)
            print(f"   트레이드 {s['trades']:,} · 승률 {s['win_rate']*100:.1f}% · 기댓값 {s['avg']*100:+.2f}%")
            print(f"   중앙값 {s['median']*100:+.2f}% · 최고 {s['best']*100:+.0f}% · 최저 {s['worst']*100:+.0f}%")
            print(f"   누적(2%씩) {s['total_return']*100:+.1f}% · MDD {s['mdd']*100:.1f}% · 트레이드샤프 {s['sharpe_per_trade']:.3f}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="검증할 종목 수(알파벳순)")
    ap.add_argument("--symbols", default="", help="쉼표 구분 종목(지정 시 limit 무시)")
    args = ap.parse_args()
    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else md.list_symbols()[:args.limit])
    print(f"=== Q 전략 정밀 재검증 (로컬 분봉 {len(syms)}종목) ===", flush=True)
    strategies = [("추세돌파(M)", "trend", "std"),
                  ("평균회귀-복귀확인(B)", "meanrev", "b"),
                  ("평균회귀-단순터치", "meanrev", "a"),
                  ("모멘텀(MACD+RSI)", "momentum", "std")]
    run(syms, strategies)


if __name__ == "__main__":
    main()
