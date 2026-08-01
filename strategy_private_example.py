"""strategy_private_example.py — 커밋되는 템플릿(플레이스홀더).

실제 전략(봇 페르소나 프롬프트·지표 튜닝값)은 gitignore된 strategy_private.py에 있음.
이 파일은 (1) 구조·문서 유지 (2) strategy_private.py 없는 환경(공개 클론)에서 앱이
더미로라도 돌아가게 하는 폴백. 봇 이름/모델/색 등 비민감 구조는 유지하고 system 프롬프트만 마스킹.
"""

AGENT_PROFILES = {'드가자': {'model_provider': 'claude',
         'model_id': 'claude-haiku-4-5-20251001',
         'color': '#ef4444',
         'description': '[비공개]',
         'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 'INTJ': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#3b82f6',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '퀀트중독자': {'model_provider': 'claude',
           'model_id': 'claude-haiku-4-5-20251001',
           'color': '#8b5cf6',
           'description': '[비공개]',
           'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '역추세봇': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#f59e0b',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '차트천재': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#10b981',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '원칙주의자': {'model_provider': 'claude',
           'model_id': 'claude-haiku-4-5-20251001',
           'color': '#f97316',
           'description': '[비공개]',
           'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '기본농부': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#06b6d4',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '감독': {'model_provider': 'claude',
        'model_id': 'claude-haiku-4-5-20251001',
        'color': '#a855f7',
        'description': '[비공개]',
        'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '류': {'model_provider': 'claude',
       'model_id': 'claude-haiku-4-5-20251001',
       'color': '#f43f5e',
       'description': '[비공개]',
       'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '알렉스': {'model_provider': 'claude',
         'model_id': 'claude-haiku-4-5-20251001',
         'color': '#0ea5e9',
         'description': '[비공개]',
         'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '실적왕': {'model_provider': 'claude',
         'model_id': 'claude-haiku-4-5-20251001',
         'color': '#22d3ee',
         'description': '[비공개]',
         'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '추세질주': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#fb7185',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '테마사냥꾼': {'model_provider': 'claude',
           'model_id': 'claude-haiku-4-5-20251001',
           'color': '#f472b6',
           'description': '[비공개]',
           'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '세력추적': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#c084fc',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '바닥픽': {'model_provider': 'claude',
         'model_id': 'claude-haiku-4-5-20251001',
         'color': '#38bdf8',
         'description': '[비공개]',
         'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '칼손절': {'model_provider': 'claude',
         'model_id': 'claude-haiku-4-5-20251001',
         'color': '#94a3b8',
         'description': '[비공개]',
         'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'}}

_TRADE_DECISION = """[비공개 — 예시] 결론 형식: [결정] 매수|관망|매도 | 이유: 한 줄.{evolution_notes}"""

TRADING_PROFILES = {'추세질주': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#fb7185',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '테마사냥꾼': {'model_provider': 'claude',
           'model_id': 'claude-haiku-4-5-20251001',
           'color': '#f472b6',
           'description': '[비공개]',
           'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '세력추적': {'model_provider': 'claude',
          'model_id': 'claude-haiku-4-5-20251001',
          'color': '#c084fc',
          'description': '[비공개]',
          'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '바닥픽': {'model_provider': 'claude',
         'model_id': 'claude-haiku-4-5-20251001',
         'color': '#38bdf8',
         'description': '[비공개]',
         'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'},
 '칼손절': {'model_provider': 'claude',
         'model_id': 'claude-haiku-4-5-20251001',
         'color': '#94a3b8',
         'description': '[비공개]',
         'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'}}

# ── AI펀드 발굴 3인(P/W/S) — 실제 방법론 프레임워크는 strategy_private.py(비공개) ──
for _k, _c in (("P", "#5aa9e6"), ("W", "#c9a227"), ("S", "#e08a3c"), ("H", "#a78bfa")):
    AGENT_PROFILES[_k] = {
        'model_provider': 'claude', 'model_id': 'claude-sonnet-5', 'color': _c,
        'description': '실적/성장 게이트' if _k == "H" else '발굴',
        'system': '[비공개 전략 — 실제 프롬프트는 strategy_private.py. 이건 example 더미.]'}

# ── 지표·튜닝 파라미터 (예시값 — 실제 튜닝값은 strategy_private.py) ──
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9   # 표준값
RSI_PERIOD = 14
RSI_MOMENTUM_MIN = 50.0
BB_PERIOD, BB_STD = 20, 2.0
MOM_GC_LOOKBACK = 5
MOM_STOP_LOOKBACK = 5
WEIGHT_PCT = 0.10        # 예시
MAX_POSITIONS = 5        # 예시
HARD_STOP_PCT = -15.0    # 예시
FUND_STOP_PCT = -0.20    # AI펀드 T 하드 스탑로스 안전망 (예시값, 실제는 strategy_private.py)
ROUND_TRIP_BPS = 10.0    # 백테스트 왕복 거래비용(bp) 예시 — 실제는 strategy_private.py
MINERVINI_STOP_PCT = -0.08   # M 타이트 스탑 예시
MINERVINI_BREAKOUT = 50      # M 신고가 돌파 룩백 예시

# 공급망 병목 시드 — 실제 종목은 strategy_private.py(비공개). 공개/폴백은 빈 값.
US_BOTTLENECK_SEED = []

# 대형주 유니버스 — 고정 섹터×중심주. 실제 큐레이션은 strategy_private.py. 폴백은 공개 메가캡.
LARGECAP_UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA"]

# 병목 자문 봇 — 실제 페르소나는 strategy_private.py. 공개/폴백은 빈 이름(비활성).
BOTTLENECK_ADVISOR_NAME = ""
