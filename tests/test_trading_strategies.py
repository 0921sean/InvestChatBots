"""트레이딩 규칙엔진(trading_strategies) 검증 — 명세 §2 각 규칙을 테스트로 증명."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trading_strategies as ts


# ── 지표 기본 정합성 ─────────────────────────────────────────
def test_bollinger_basic():
    lower, mid, upper = ts.bollinger([10] * 19 + [10])
    assert mid == 10 and lower == 10 and upper == 10   # 무변동 → 밴드 폭 0

def test_rsi_all_up_is_100():
    assert ts.rsi([float(i) for i in range(1, 20)]) == 100.0

def test_rsi_all_down_is_0():
    assert ts.rsi([float(i) for i in range(20, 0, -1)]) == 0.0


# ── 진입 신호 ────────────────────────────────────────────────
def test_momentum_entry_on_golden_cross(monkeypatch):
    # 마지막 봉 MACD 골든크로스(-1<=0 → 0.5>0) + RSI≥50 → 진입
    monkeypatch.setattr(ts, "macd_series", lambda c: ([-1.0, 0.5], [0.0, 0.0]))
    monkeypatch.setattr(ts, "rsi", lambda c, period=ts.RSI_PERIOD: 60.0)
    assert ts.momentum_entry([0.0] * 40) is True

def test_momentum_entry_blocked_by_low_rsi(monkeypatch):
    monkeypatch.setattr(ts, "macd_series", lambda c: ([-1.0, 0.5], [0.0, 0.0]))
    monkeypatch.setattr(ts, "rsi", lambda c, period=ts.RSI_PERIOD: 45.0)
    assert ts.momentum_entry([0.0] * 40) is False   # 크로스는 떴지만 RSI 게이트 미달

def test_momentum_no_signal_when_flat():
    assert ts.momentum_entry([100.0] * 40) is False

def test_meanrev_entry_on_recovery():
    # §3 복귀확인: 직전 봉 하단 이탈(82) → 당일 종가 복귀(100) → 진입
    closes = [100.0] * 36 + [100, 100, 82, 100]
    assert ts.meanrev_entry(closes) is True

def test_meanrev_no_entry_on_break_without_recovery():
    # 하단 이탈만·복귀 전(당일도 하단 아래) → 진입 안 함 (칼받기 방지)
    closes = [100.0] * 36 + [100, 100, 100, 82]
    assert ts.meanrev_entry(closes) is False

def test_meanrev_no_entry_when_above_band():
    assert ts.meanrev_entry([100.0] * 40) is False

def test_scan_entries_needs_min_bars():
    assert ts.scan_entries([100.0] * 10) == []      # 데이터 부족 → 신호 없음


# ── 청산: 연 전략 규칙으로만 (⚠️ 사보타주 방지) ───────────────
def _revert_series():
    """평균회귀 청산 True(중심선 회복) + 모멘텀 청산 False(막판 상승이라 데드크로스 아님)."""
    return [95.0] * 36 + [93, 95, 98, 101]

def test_meanrev_exit_take_profit_at_mid():
    s = _revert_series()
    ok, reason = ts.should_exit(ts.MEANREV, s, pnl_pct=4.0)
    assert ok is True and "익절" in reason           # 중심선 도달 익절

def test_meanrev_stop_on_redrop_below_lower():
    # 진입 후 다시 하단밴드 아래 이탈 → 복귀 실패 손절 (하드스톱 이전에)
    closes = [100.0] * 36 + [100, 100, 100, 82]
    ok, reason = ts.should_exit(ts.MEANREV, closes, pnl_pct=-3.0)
    assert ok is True and "손절" in reason

def test_momentum_position_not_closed_by_meanrev_rule():
    s = _revert_series()
    assert ts.meanrev_exit(s) is True               # 역추세 규칙은 청산이라 하지만
    exit_now, _ = ts.should_exit(ts.MOMENTUM, s, pnl_pct=3.0)
    assert exit_now is False                         # 모멘텀 포지션은 그 규칙으로 안 닫힘

def test_momentum_exit_on_dead_cross(monkeypatch):
    # 마지막 봉 데드크로스(1>=0 → -0.5<0)
    monkeypatch.setattr(ts, "macd_series", lambda c: ([1.0, -0.5], [0.0, 0.0]))
    closes = [100.0] * 40
    assert ts.momentum_exit(closes) is True
    exit_now, reason = ts.should_exit(ts.MOMENTUM, closes, pnl_pct=1.0)
    assert exit_now is True and "데드크로스" in reason

def test_meanrev_position_not_closed_by_momentum_rule(monkeypatch):
    monkeypatch.setattr(ts, "macd_series", lambda c: ([1.0, -0.5], [0.0, 0.0]))  # 모멘텀 데드크로스 상태
    closes = [100.0] * 20 + [104 - 0.4 * i for i in range(20)]  # 종가 하단<c<중심선 → 볼린저 청산 미충족
    assert ts.momentum_exit(closes) is True          # 모멘텀 규칙으론 청산이지만
    assert ts.meanrev_exit(closes) is False          # 역추세 청산 조건 자체는 미충족
    exit_now, _ = ts.should_exit(ts.MEANREV, closes, pnl_pct=2.0)
    assert exit_now is False                          # 역추세 포지션은 모멘텀 규칙 무시


# ── -20% 하드스톱 백스톱 (전략 무관, 최우선) ──────────────────
def test_hard_stop_triggers_regardless_of_strategy():
    flat = [100.0] * 40
    for strat in (ts.MOMENTUM, ts.MEANREV, "legacy_unknown"):
        exit_now, reason = ts.should_exit(strat, flat, pnl_pct=-20.0)
        assert exit_now is True and "하드스톱" in reason

def test_hard_stop_not_triggered_above_threshold():
    exit_now, _ = ts.should_exit(ts.MOMENTUM, [100.0] * 40, pnl_pct=-19.9)
    assert exit_now is False

def test_legacy_position_only_hard_stop():
    # 태그 없는 포지션은 어떤 지표 청산도 아니고 하드스톱만
    s = _revert_series()
    assert ts.should_exit("", s, pnl_pct=-5.0) == (False, "")
    assert ts.should_exit("", s, pnl_pct=-20.0)[0] is True


# ── 사이징 / 상한 (§4) ───────────────────────────────────────
def test_position_amount_is_weight_of_capital():
    assert ts.position_amount(25_000_000) == 25_000_000 * ts.WEIGHT_PCT

def test_weight_within_spec_range():
    assert 0.10 <= ts.WEIGHT_PCT <= 0.20            # §4: 종목당 10~20%

def test_max_positions_within_spec_range():
    assert 5 <= ts.MAX_POSITIONS <= 10             # §4: 동시 5~10

def test_can_open_respects_cap():
    assert ts.can_open(ts.MAX_POSITIONS - 1) is True
    assert ts.can_open(ts.MAX_POSITIONS) is False
