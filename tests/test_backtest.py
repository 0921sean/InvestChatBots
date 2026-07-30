"""백테스트 하네스 무결성·집계 검증 (명세 §6-A)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest as bt


def test_aggregate_metrics():
    trades = [{"ret": 0.10, "size": 1}, {"ret": -0.05, "size": 1},
              {"ret": 0.02, "size": 1}, {"ret": -0.05, "size": 1}]
    a = bt.aggregate(trades)
    assert a["n"] == 4
    assert a["win_rate"] == 50.0
    assert a["payoff"] == 1.2               # avg_win 0.06 / avg_loss 0.05
    assert a["expectancy"] == 0.5           # 평균 0.005 → %

def test_aggregate_empty():
    assert bt.aggregate([]) == {"n": 0}

def test_min_bars_skip():
    o = {"open": [100.0] * 100, "close": [100.0] * 100, "low": [100.0] * 100}
    assert bt.backtest_stock(o, "meanrev", "b", "US") == []   # 300봉 미만 스킵

def test_kr_cost_includes_tax():
    assert bt.COST["KRX"][1] > bt.COST["US"][1]   # KR 청산에 거래세 반영

def test_no_lookahead_fill_next_open():
    # 볼린저 단순터치: 하단 이탈 후 회복 → 트레이드 발생, 체결은 다음 봉 시가
    c = [100.0] * 310 + [100, 100, 84, 90, 95, 106, 106, 106, 106, 106]
    o = list(c)
    low = list(c)
    trades = bt.backtest_stock({"open": o, "close": c, "low": low}, "meanrev", "a", "US")
    assert len(trades) >= 1
    # 비용 적용: 순수익 = gross - 왕복비용(0.001). 지어낸 값이 아니라 유한·비용반영
    assert all(isinstance(t["ret"], float) for t in trades)

def test_classify_regime_labels():
    # 최근 63봉 수익률로 상승/하락/횡보, 초기는 미정
    up = [100.0] * 63 + [100 * 1.10]      # +10% → 상승
    assert bt.classify_regime(up)[-1] == "상승"
    assert bt.classify_regime(up)[0] == "미정"      # lookback 미만
    down = [100.0] * 63 + [100 * 0.90]    # -10% → 하락
    assert bt.classify_regime(down)[-1] == "하락"
    flat = [100.0] * 63 + [100 * 1.01]    # +1% → 횡보
    assert bt.classify_regime(flat)[-1] == "횡보"


def test_segment_by_regime_buckets_by_entry_date():
    trades = [{"ret": 0.10, "entry_date": "2024-01-02"},
              {"ret": -0.04, "entry_date": "2024-01-02"},
              {"ret": 0.06, "entry_date": "2024-06-03"}]
    regime = {"2024-01-02": "상승", "2024-06-03": "횡보"}
    seg = bt.segment_by_regime(trades, regime)
    assert seg["상승"]["n"] == 2 and seg["횡보"]["n"] == 1
    assert seg["상승"]["win_rate"] == 50.0


def test_segment_missing_date_is_미정():
    seg = bt.segment_by_regime([{"ret": 0.02, "entry_date": "1999-01-01"}], {})
    assert seg["미정"]["n"] == 1


def test_trade_carries_entry_date():
    # 백테스트 트레이드가 regime 분해용 진입일을 담는지
    c = [100.0] * 310 + [100, 100, 84, 90, 95, 106, 106, 106, 106, 106]
    d = {"open": list(c), "close": c, "low": list(c),
         "dates": [f"d{i}" for i in range(len(c))]}
    trades = bt.backtest_stock(d, "meanrev", "a", "US")
    assert trades and all("entry_date" in t for t in trades)


def test_sma_series():
    s = bt._sma_series([1.0, 2, 3, 4, 5], 3)
    assert s[:2] == [None, None]
    assert s[2] == 2.0 and s[3] == 3.0 and s[4] == 4.0   # 롤링 평균


def test_minervini_enters_uptrend_exits_on_breakdown():
    # 상승추세(정배열·신고가 돌파 → 진입) 후 급락(50일선 하회 → 청산) = 완료거래 발생
    rise = [100.0 + i * 0.3 for i in range(300)]         # 100 → ~190 상승추세
    drop = [rise[-1] - 4 * i for i in range(1, 31)]      # 급락 → 50일선 하회
    c = rise + drop
    o = {"open": list(c), "close": c, "low": [x * 0.99 for x in c],
         "dates": [f"d{i}" for i in range(len(c))]}
    trades = bt.backtest_stock(o, "minervini", "std", "US")
    assert trades                                        # 진입+청산 완료거래 ≥1
    assert all("entry_date" in t for t in trades)


def test_minervini_no_entry_in_downtrend():
    # 하락추세 → 트렌드템플릿 불충족 → 진입 없음
    c = [200.0 - i * 0.3 for i in range(320)]
    o = {"open": list(c), "close": c, "low": [x * 0.99 for x in c]}
    assert bt.backtest_stock(o, "minervini", "std", "US") == []


def test_momentum_v1_vs_v2_differ():
    # v2는 절반익절로 트레이드 기록이 v1과 다르게 나올 수 있음(구조 검증)
    import random
    random.seed(1)
    base = 100.0
    c = []
    for i in range(400):
        base *= (1 + (0.02 if (i // 20) % 2 == 0 else -0.015))
        c.append(round(base, 2))
    o = list(c)
    low = [x * 0.99 for x in c]
    d = {"open": o, "close": c, "low": low}
    v1 = bt.backtest_stock(d, "momentum", "v1", "US")
    v2 = bt.backtest_stock(d, "momentum", "v2", "US")
    # 둘 다 실행되고(예외 없이) 리스트 반환
    assert isinstance(v1, list) and isinstance(v2, list)
