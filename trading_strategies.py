"""
트레이딩 계좌 규칙엔진 (블랙박스 LLM 제거) — 명세 §2.

두 전략을 순수 규칙으로 정의. 진입은 독립 병렬(§2.1: 하나라도 신호 → 매수),
청산은 포지션에 연(緣)한 전략의 규칙으로만(§2.2: "하나라도 매도" 금지 — 모멘텀 vs
역추세 사보타주 방지). -20% 하드스톱은 전략 무관 무조건 청산 백스톱.

  · 차트천재  = MACD+RSI 모멘텀(추세추종)          strategy='momentum'
  · 역추세봇  = 평균회귀 20·2 평균회귀(과매도 반등)   strategy='meanrev'   (구 '빅픽처' 개명)

파라미터는 아래 상수 = 업계 표준 기본값. 명세 본문(§2.3/§2.4/§4) 확정 수치가 오면
이 상수만 교체하면 된다. 지표는 외부 의존 없이 순수 파이썬(테스트 결정성·속도).
입력은 '종가 리스트'(오래된→최신), 일봉 기준.
"""
from __future__ import annotations

# ── 파라미터 (명세 오면 여기만 교체) ─────────────────────────
# 지표·튜닝 파라미터는 비공개(strategy_private.py). 없으면 example 더미.
try:
    from strategy_private import (MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_PERIOD,
        RSI_MOMENTUM_MIN, BB_PERIOD, BB_STD, MOM_GC_LOOKBACK, MOM_STOP_LOOKBACK,
        WEIGHT_PCT, MAX_POSITIONS, HARD_STOP_PCT)
except ImportError:
    from strategy_private_example import (MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_PERIOD,
        RSI_MOMENTUM_MIN, BB_PERIOD, BB_STD, MOM_GC_LOOKBACK, MOM_STOP_LOOKBACK,
        WEIGHT_PCT, MAX_POSITIONS, HARD_STOP_PCT)

MOMENTUM, MEANREV = "momentum", "meanrev"
_MIN_BARS = MACD_SLOW + MACD_SIGNAL + 2   # 필요한 최소 종가 개수


# ── 순수 지표 ────────────────────────────────────────────────
def ema_series(values: list[float], period: int) -> list[float]:
    """EMA 시계열. seed = 첫 period 단순평균."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd_series(closes: list[float]):
    """(macd_line, signal_line) — 동일 길이로 끝 정렬해 반환."""
    fast = ema_series(closes, MACD_FAST)
    slow = ema_series(closes, MACD_SLOW)
    if not fast or not slow:
        return [], []
    # fast가 slow보다 (SLOW-FAST)만큼 길다 → 뒤에서 맞춤
    n = min(len(fast), len(slow))
    macd_line = [fast[-n + i] - slow[-n + i] for i in range(n)]
    signal = ema_series(macd_line, MACD_SIGNAL)
    if not signal:
        return macd_line, []
    m = len(signal)
    return macd_line[-m:], signal


def rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float]:
    """Wilder RSI 시계열(index=period부터)."""
    if len(closes) < period + 1:
        return []
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g, avg_l = gains / period, losses / period
    val = lambda ag, al: 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    out = [val(avg_g, avg_l)]
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0.0)) / period
        out.append(val(avg_g, avg_l))
    return out


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder RSI(마지막 값)."""
    s = rsi_series(closes, period)
    return s[-1] if s else None


def bollinger(closes: list[float], period: int = BB_PERIOD, std: float = BB_STD):
    """(lower, mid, upper) 마지막 값. mid = 단순이동평균."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    sd = var ** 0.5
    return mid - std * sd, mid, mid + std * sd


# ── 진입 신호 (독립 병렬) ────────────────────────────────────
def _gc_within(macd_line, signal, lookback) -> bool:
    """최근 lookback 봉 안에 골든크로스(정배열 진입)가 있었나."""
    n = min(len(macd_line), len(signal))
    for i in range(max(1, n - lookback), n):
        if macd_line[i - 1] <= signal[i - 1] and macd_line[i] > signal[i]:
            return True
    return False


def momentum_signal(closes: list[float]):
    """§2.3 GC + RSI 3분기 진입. 발화 종류 'A'/'B'/'C' 또는 None.
      A(정석): GC + 50≤RSI<70 → 즉시.
      B(약함): GC(최근) + 정배열 유지 + RSI가 50 상향돌파하는 캔들.
      C(과열): GC(최근) + 정배열 유지 + RSI 70 이력 후 50 부근(45~55) 지지."""
    macd_line, signal = macd_series(closes)
    rs = rsi_series(closes)
    if len(macd_line) < 2 or len(signal) < 2 or len(rs) < 2:
        return None
    aligned = macd_line[-1] > signal[-1]                       # 정배열
    gc_now = macd_line[-2] <= signal[-2] and macd_line[-1] > signal[-1]
    r, prev_r = rs[-1], rs[-2]
    if gc_now and 50 <= r < 70:
        return "A"
    if not (aligned and _gc_within(macd_line, signal, MOM_GC_LOOKBACK)):
        return None
    if prev_r < 50 <= r:                                       # RSI 50 상향돌파
        return "B"
    if max(rs[-MOM_GC_LOOKBACK:]) > 70 and 45 <= r <= 55:      # 과열 후 지지
        return "C"
    return None


def momentum_entry(closes: list[float]) -> bool:
    return momentum_signal(closes) is not None


def momentum_stop(lows: list[float], lookback: int = MOM_STOP_LOOKBACK) -> float:
    """§2.3 손절가 = 진입 근처 최근 저점."""
    return min(lows[-lookback:]) if lows else 0.0


def momentum_target(entry: float, stop: float) -> float:
    """§2.3 목표 = 손절폭 × 2 (손익비 1:2)."""
    return entry + 2.0 * (entry - stop)


def meanrev_entry(closes: list[float]) -> bool:
    """§3 복귀확인 진입: 직전 종가가 하단밴드 아래로 이탈 → 당일 종가가 하단밴드 위로 복귀.
    (단순 터치/이탈만으론 진입 안 함 = 칼받기 방지.)"""
    if len(closes) < BB_PERIOD + 1:
        return False
    prev = bollinger(closes[:-1])   # 직전 봉 기준 밴드
    cur = bollinger(closes)         # 당일 봉 기준 밴드
    if not prev or not cur:
        return False
    return closes[-2] < prev[0] and closes[-1] >= cur[0]


def scan_entries(closes: list[float]) -> list[str]:
    """이 종목에서 발화한 전략 목록. 둘 다 뜨면 둘 다(호출측이 1포지션 규칙 적용)."""
    if len(closes) < _MIN_BARS:
        return []
    fired = []
    if momentum_entry(closes):
        fired.append(MOMENTUM)
    if meanrev_entry(closes):
        fired.append(MEANREV)
    return fired


# ── 청산 (연 전략 규칙으로만 + 하드스톱 백스톱) ───────────────
def momentum_exit(closes: list[float]) -> bool:
    """§2.3 잔량 청산 신호: MACD 데드크로스 OR RSI 50 하향이탈."""
    macd_line, signal = macd_series(closes)
    rs = rsi_series(closes)
    dc = (len(macd_line) >= 2 and len(signal) >= 2
          and macd_line[-2] >= signal[-2] and macd_line[-1] < signal[-1])
    rsi_down = len(rs) >= 2 and rs[-2] >= 50 and rs[-1] < 50
    return bool(dc or rsi_down)


def meanrev_exit(closes: list[float]) -> bool:
    """§4: 익절(종가 ≥ 중심선; 상단밴드 포함) 또는 손절(종가 < 하단밴드 = 복귀 실패)."""
    reason = meanrev_exit_reason(closes)
    return reason is not None


def meanrev_exit_reason(closes: list[float]):
    """청산 사유 문자열 or None. 익절=중심선 이상, 손절=하단 재이탈."""
    bb = bollinger(closes)
    if not bb:
        return None
    lower, mid, _ = bb
    c = closes[-1]
    if c >= mid:
        return "중심선 익절"
    if c < lower:
        return "복귀 실패 손절"
    return None


def momentum_manage(closes, price, entry, stop, half_exited, pnl_pct):
    """§2.3 모멘텀 v2 관리 → (action, 사유). action ∈ 'hold'|'half'|'full'.
    손절=전량 / 목표(1:2) 도달=절반익절 / 추세이탈(DC·RSI50)=전량 / 절반후 본전=잔량 전량.
    백테스트 검증본과 동일 규칙. -20% 하드스톱은 전량 최우선."""
    if pnl_pct is not None and pnl_pct <= HARD_STOP_PCT:
        return "full", f"-20% 하드스톱 ({pnl_pct:.1f}%)"
    if stop is not None and price is not None and price <= stop:
        return "full", "손절(최근 저점)"
    if (not half_exited and stop is not None and entry is not None and price is not None
            and price >= momentum_target(entry, stop)):
        return "half", "목표 1:2 절반 익절"
    if len(closes) >= _MIN_BARS and momentum_exit(closes):
        return "full", "추세 이탈(DC·RSI50)"
    if half_exited and entry is not None and price is not None and price <= entry:
        return "full", "본전 회귀(잔량 청산)"
    return "hold", ""


def should_exit(strategy: str, closes: list[float], pnl_pct: float,
                price: float = None, stop: float = None, entry: float = None):
    """(청산여부, 사유). 전량 청산 v1(부분매도 없음). 하드스톱은 전략 무관 최우선.
    모멘텀은 진입 시 저장한 손절가(stop)·현재가(price)·진입가(entry)로 손절/목표를 판정."""
    if pnl_pct is not None and pnl_pct <= HARD_STOP_PCT:
        return True, f"-20% 하드스톱 ({pnl_pct:.1f}%)"
    if len(closes) < _MIN_BARS:
        return False, ""
    if strategy == MOMENTUM:
        if stop is not None and price is not None and price <= stop:
            return True, "손절(최근 저점)"
        if stop is not None and entry is not None and price is not None \
                and price >= momentum_target(entry, stop):
            return True, "목표 1:2 익절"
        if momentum_exit(closes):
            return True, "추세 이탈(DC·RSI50)"
        return False, ""
    if strategy == MEANREV:
        r = meanrev_exit_reason(closes)
        return (True, r) if r else (False, "")
    return False, ""   # 태그 없는 레거시 포지션은 하드스톱만 적용


# ── 사이징 (§4) ──────────────────────────────────────────────
def position_amount(capital: float) -> float:
    """종목당 매수 금액 = 자본 × 비중."""
    return capital * WEIGHT_PCT


def can_open(open_count: int) -> bool:
    """동시 보유 상한 체크."""
    return open_count < MAX_POSITIONS
