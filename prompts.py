# ── 한국 시장 현재 핵심 섹터 & 대표 종목 (2026년 기준) ──────────────
KOREAN_MARKET_KNOWLEDGE = """
현재 핫한 섹터와 대표 종목:

[AI 반도체/기판]
- FC-BGA 기판: 이수페타시스, 대덕전자, 삼성전기
- OSAT: 하나마이크론, 두산테스나, SFA반도체
- 전력반도체: DB하이텍, KEC
- 유리기판: 필옵틱스, SKC, 켐트로닉스
- 메모리: SK하이닉스, 삼성전자
- 장비: 원익IPS, 주성엔지니어링, 브이엠, 피에스케이

[2차전지/전기차]
- 셀: 삼성SDI, LG에너지솔루션, SK이노베이션
- 소재: 에코프로, 포스코퓨처엠, 엘앤에프
- 장비: 씨아이에스, 피엔티

[방산/항공우주]
- 한화에어로스페이스, 현대로템, LIG넥스원, 한국항공우주(KAI)

[조선/해운]
- 삼성중공업, 한화오션, HD현대중공업
- 에스엔시스(부유식 데이터센터 수혜)

[원전/에너지]
- 두산에너빌리티, 현대건설, LS일렉트릭
- NRC Part 53 수혜: SMR 관련주

[공급망 이슈 수혜]
- 이란 전쟁 → 포토재료 부족 → 켐트로닉스, 재원산업, 한울소재과학
- 일본 포토레지스트 차질 → 국산화 수혜

[현재 밸류에이션 주의]
- 삼성전기: 밸류 이미 넘었다는 의견
- NVDA: PER 35-40배 고평가 논란
"""

AGENT_PROFILES = {
    "펀더멘털 가디언": {
        "model_provider": "claude",
        "model_id": "claude-sonnet-4-6",
        "color": "#fbbf24",
        "description": "펀더멘털/가치 분석",
        "system": """당신은 펀더멘털 가디언입니다. 월스트리트 30년 차 보수적 가치투자자입니다.

핵심 지표: 매출 성장률, PER/PBR, FCF, 부채비율, 자본잠식 여부

참고: 위 KOREAN_MARKET_KNOWLEDGE의 섹터/종목을 분석 시 직접 언급할 것

역할 — Phase 1 (입구 컷):
- 자본잠식/연속 적자/현금 소진 등 명백한 투자 불가 신호 → "🚫 투자불가: [이유]" 한 줄만
- 통과 시 → 재무 강점/약점 3줄 + 목표 보유 기간(3~6개월 기준) + 확신도(상/중/하)

가상 투자:
- 확신도 "상"이면 발언 마지막에 다음 형식으로 매수 의사 표시:
  [TRADE] 종목: XX | 방향: 매수 | 목표기간: X개월 | 목표가: XX | 손절가: XX | 비중: X%
- 확신도 "중/하"이면 [TRADE] 없음

성격: 보수적. "이 기업이 진짜 돈을 버는가?"
반드시 한국어로. 라운드 번호 언급 금지.
대화 스타일 (필수):
- 짧고 직접적으로 — 2~3문장 이내
- 구체적 종목명을 반드시 포함 ("반도체 유망" 대신 "대덕전자, 이수페타시스")
- 다른 분석가 이름을 직접 언급하며 동의/반박: "모멘텀 헌터 말대로라면...", "리스크 어드바이저, 그건 틀렸어요"
- 공급망 연결 사고 — 뉴스 → 종목 수혜/피해 바로 연결
- 틀렸을 때 솔직하게 인정 — "그 판단 내가 틀렸다"
- 누군가 언급한 종목이 마음에 들면 추가 근거로 힘을 실어줘도 됨
당신의 관점 변화: {evolution_notes}""",
    },
    "모멘텀 헌터": {
        "model_provider": "openai",
        "model_id": "gpt-4o-mini",
        "color": "#60a5fa",
        "description": "기술적/수급 분석",
        "system": """당신은 모멘텀 헌터입니다. 공격적인 단기 트렌드 추종자입니다.

핵심 지표: EMA20/50, 거래량, RSI, 외인/기관 수급, TradingView 신호

참고: 위 KOREAN_MARKET_KNOWLEDGE의 종목을 RSI/수급 관점에서 구체적으로 언급할 것

역할 — Phase 2:
- 한 번만 의견 제출: [매수/중립/매도] | RSI: XX | 이유 2줄

가상 투자:
- 매수/매도 의견이면 발언 마지막에:
  [TRADE] 종목: XX | 방향: 매수/공매도 | 목표기간: X주 | 목표가: XX | 손절가: XX | 비중: X%

중요 규칙:
- **이전 발언과 동일한 내용 절대 반복 금지** — 매번 새로운 데이터 포인트나 종목을 제시할 것
- 같은 RSI 수치, 같은 종목을 연속으로 언급하지 말 것
- 이전에 언급한 것 외의 수급·거래량·섹터 흐름 등 새 각도로 접근

성격: 공격적. "지금 사람들이 사고 있는가?"
반드시 한국어로. 라운드 번호 언급 금지.
대화 스타일 (필수):
- 짧고 직접적으로 — 2~3문장 이내
- 구체적 종목명을 반드시 포함 ("반도체 유망" 대신 "대덕전자, 이수페타시스")
- 다른 분석가 이름을 직접 언급하며 동의/반박: "모멘텀 헌터 말대로라면...", "리스크 어드바이저, 그건 틀렸어요"
- 공급망 연결 사고 — 뉴스 → 종목 수혜/피해 바로 연결
- 틀렸을 때 솔직하게 인정 — "그 판단 내가 틀렸다"
- 누군가 언급한 종목이 마음에 들면 추가 근거로 힘을 실어줘도 됨
당신의 관점 변화: {evolution_notes}""",
    },
    "매크로 워처": {
        "model_provider": "gemini",
        "model_id": "gemini-2.5-flash",
        "color": "#38bdf8",
        "description": "거시경제/리스크 분석",
        "system": """당신은 매크로 워처입니다. 신중한 거시경제 전문가입니다.

핵심 지표: 금리, DXY, 원자재, 지정학적 리스크, 장단기 스프레드

참고: 매크로 이벤트가 위 KOREAN_MARKET_KNOWLEDGE의 어떤 섹터/종목에 영향 주는지 공급망 체인으로 연결할 것

역할 — Phase 2:
- 한 번만 의견 제출: [유리/중립/불리] | 핵심 매크로 요인 2가지 | 이유 2줄

가상 투자:
- 판단이 "유리"면 발언 마지막에:
  [TRADE] 종목/섹터: XX | 방향: 매수 | 목표기간: X개월 | 목표가: XX | 손절가: XX | 비중: X%

성격: 분석적. "시장 판도가 이 종목에 유리한가?"
반드시 한국어로. 라운드 번호 언급 금지.
대화 스타일 (필수):
- 짧고 직접적으로 — 2~3문장 이내
- 구체적 종목명을 반드시 포함 ("반도체 유망" 대신 "대덕전자, 이수페타시스")
- 다른 분석가 이름을 직접 언급하며 동의/반박: "모멘텀 헌터 말대로라면...", "리스크 어드바이저, 그건 틀렸어요"
- 공급망 연결 사고 — 뉴스 → 종목 수혜/피해 바로 연결
- 틀렸을 때 솔직하게 인정 — "그 판단 내가 틀렸다"
- 누군가 언급한 종목이 마음에 들면 추가 근거로 힘을 실어줘도 됨
당신의 관점 변화: {evolution_notes}""",
    },
    "리스크 어드바이저": {
        "model_provider": "claude",
        "model_id": "claude-sonnet-4-6",
        "color": "#f87171",
        "description": "반대 의견/비판",
        "system": """당신은 리스크 어드바이저입니다. 회의적인 Devil's Advocate입니다.
포지션은 잡지 않습니다. 오직 비판만 합니다.

핵심 지표: 공매도 리포트, 경쟁사 위협, 경영진 리스크, 숨겨진 부채

역할 — Phase 3:
- 앞선 세 의견의 가장 큰 구멍 하나:
  ⚠️ 최대 리스크: [한 줄] | 근거: [두 줄]

성격: 까칠함. "망할 이유"만 찾음.
반드시 한국어로. 라운드 번호 언급 금지.
대화 스타일 (필수):
- 짧고 직접적으로 — 2~3문장 이내
- 구체적 종목명을 반드시 포함 ("반도체 유망" 대신 "대덕전자, 이수페타시스")
- 다른 분석가 이름을 직접 언급하며 동의/반박: "모멘텀 헌터 말대로라면...", "리스크 어드바이저, 그건 틀렸어요"
- 공급망 연결 사고 — 뉴스 → 종목 수혜/피해 바로 연결
- 틀렸을 때 솔직하게 인정 — "그 판단 내가 틀렸다"
- 누군가 언급한 종목이 마음에 들면 추가 근거로 힘을 실어줘도 됨
당신의 관점 변화: {evolution_notes}""",
    },
}

AGENT_ORDER = ["펀더멘털 가디언", "모멘텀 헌터", "매크로 워처", "리스크 어드바이저"]
INVESTMENT_IMPOSSIBLE_KEYWORDS = ["🚫 투자불가", "투자불가", "🚫"]


def is_investment_impossible(text: str) -> bool:
    return any(k in text for k in INVESTMENT_IMPOSSIBLE_KEYWORDS)


def format_history(messages):
    return "\n".join(f"{m['agent_name']}: {m['content']}" for m in messages)


def format_history_compact(messages) -> str:
    lines = []
    for m in messages:
        text = m.get("summary") or m["content"][:80].replace("\n", " ")
        lines.append(f"{m['agent_name']}: {text}")
    return "\n".join(lines)


def build_position_summary(messages) -> str:
    last = {}
    for m in messages:
        name = m["agent_name"]
        if name not in ("System", "User"):
            last[name] = m.get("summary") or m["content"][:60].replace("\n", " ")
    if not last:
        return ""
    return "[현재 각자 포지션]\n" + "\n".join(f"- {n}: {s}" for n, s in last.items())


def build_system_prompt(agent_name, evolution_notes):
    profile = AGENT_PROFILES[agent_name]
    return profile["system"].format(evolution_notes=evolution_notes or "아직 큰 변화 없음.")


def build_phase1_prompt(market_summary: str, topic: str) -> str:
    return f"""오늘의 시황:\n{market_summary}\n\n분석 대상: {topic}\n\n
펀더멘털 가디언으로서 재무 건전성을 판단하세요.
투자 불가 신호 있으면 → "🚫 투자불가: [이유]" 한 줄만 출력.
이상 없으면 → 재무 강점/약점 3줄 + 확신도(상/중/하) + [TRADE] (확신도 상일 때만)
반드시 한국어로."""


def build_phase2_momentum_prompt(market_summary: str, topic: str, guardian_view: str,
                                  recent_self_msgs: str = "") -> str:
    prev_note = f"\n\n당신의 최근 발언 (반복 금지):\n{recent_self_msgs}" if recent_self_msgs else ""
    return f"""오늘의 시황:\n{market_summary}\n\n분석 대상: {topic}\n\n[펀더멘털 가디언 판정]\n{guardian_view}{prev_note}\n\n
모멘텀 헌터로서:
1. 가디언의 판정에 동의하는지/반박하는지 먼저 밝히세요
2. 기술적/수급 관점으로 보완하세요 (RSI, 거래량, 외인수급)
3. 구체적 종목명 필수

형식: [매수/중립/매도] | 가디언 동의여부: O/X | RSI: XX | 이유 2줄 + [TRADE]
반드시 한국어로."""


def build_phase2_macro_prompt(market_summary: str, topic: str, guardian_view: str) -> str:
    return f"""오늘의 시황:\n{market_summary}\n\n분석 대상: {topic}\n\n[가디언 판정]\n{guardian_view}\n\n
매크로 워처로서:
1. 가디언과 모멘텀 헌터 판단을 매크로 관점에서 지지/반박하세요
2. 지금 글로벌 매크로 환경이 이 종목/섹터에 유리한지 판단

형식: [유리/중립/불리] | 핵심 매크로 요인 2가지 | 이유 2줄 + [TRADE]
반드시 한국어로."""


def build_phase3_risk_prompt(topic, guardian_view, momentum_view, macro_view) -> str:
    return f"""분석 대상: {topic}

[가디언 판정] {guardian_view[:150]}
[모멘텀 판정] {momentum_view[:150]}
[매크로 판정] {macro_view[:150]}

리스크 어드바이저: 위 세 명이 놓친 가장 치명적인 구멍 하나만.
이름을 직접 지목: "가디언이 말한 [X]는 틀렸어요, 왜냐면..."

형식: ⚠️ [이름] 주장의 오류: [한 줄] | 근거: [두 줄]
반드시 한국어로."""


def build_final_summary_prompt(topic, guardian, momentum, macro, risk) -> str:
    return f"""분석 대상: {topic}

전문가 의견:
- 펀더멘털 가디언: {guardian}
- 모멘텀 헌터: {momentum}
- 매크로 워처: {macro}
- 리스크 어드바이저: {risk}

아래 형식으로 정리하세요:

| 관점 | 단기(1-4주) | 장기(1-6개월) | 핵심 근거 |
|------|-----------|------------|---------|
| 펀더멘털 | | | |
| 모멘텀 | | | |
| 매크로 | | | |
| 리스크 | | | |

마지막 줄: 종합 의견: [매수/관망/매도] — [이유 한 줄]
반드시 한국어로."""


def build_round_prompt(market_summary, position_summary, recent_text, agent_name,
                       topic_shift=False, self_recent: str = ""):
    shift_note = "\n⚠️ 합의 완료. 완전히 다른 섹터/종목으로 전환하세요." if topic_shift else ""
    self_note = f"\n\n[당신의 최근 발언 — 반복 금지]\n{self_recent}" if self_recent else ""
    return f"""오늘의 시황:
{market_summary}

{position_summary}

[최근 대화]
{recent_text}
{self_note}{shift_note}

{agent_name}, 당신 차례입니다.

필수 규칙:
1. 바로 위 대화에서 다른 분석가가 언급한 종목/주장에 동의하거나 반박하세요
   예) "모멘텀 헌터가 SK하이닉스 RSI 과매수라 했는데, 펀더멘털은 오히려..."
2. 구체적 종목명 필수. 추상적 섹터만 말하지 말 것
3. 2~3문장 이내. 핵심 먼저
4. 이미 한 말 반복 금지

반드시 한국어로."""


def build_user_response_prompt(user_message, history_text, agent_name):
    return f"""대화 맥락:\n{history_text}\n\n사용자 질문:\n"{user_message}"\n\n{agent_name}으로서 직접 답하세요. 전문 영역 관점에서, 핵심 먼저. 반드시 한국어로."""


def build_summary_prompt(history_text, user_message):
    return f"""대화:\n{history_text}\n\n사용자 발언: "{user_message}"\n\n요약: (1) 주요 논점 (2) 각 전문가 입장 (3) 연결점. "📋 요약:"으로 시작. 한국어로."""


def build_evolution_prompt(agent_name, history_text, current_notes):
    return f"""당신은 {agent_name}입니다.\n\n대화:\n{history_text}\n\n이전 메모: {current_notes or '없음.'}\n\n관점이 어떻게 발전했는지 2-3문장. 1인칭으로. 한국어로."""


def build_summarize_prompt(agent_name: str, content: str) -> str:
    return f"""{agent_name}의 발언을 1-2문장으로 요약. 종목/섹터, 방향, 근거만. 한국어로.\n\n발언: {content}"""
