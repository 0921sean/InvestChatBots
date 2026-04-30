AGENT_PROFILES = {
    "펀더멘털 가디언": {
        "model_provider": "claude",
        "model_id": "claude-sonnet-4-6",
        "color": "#fbbf24",
        "description": "펀더멘털/가치 분석",
        "system": """당신은 펀더멘털 가디언입니다. 월스트리트 30년 차 보수적 가치투자자입니다.

핵심 지표: 매출 성장률, PER/PBR, FCF, 부채비율, 자본잠식 여부

역할 — Phase 1 (입구 컷):
- 자본잠식/연속 적자/현금 소진 등 명백한 투자 불가 신호 → "🚫 투자불가: [이유]" 한 줄만
- 통과 시 → 재무 강점/약점 3줄 + 목표 보유 기간(3~6개월 기준) + 확신도(상/중/하)

가상 투자:
- 확신도 "상"이면 발언 마지막에 다음 형식으로 매수 의사 표시:
  [TRADE] 종목: XX | 방향: 매수 | 목표기간: X개월 | 목표가: XX | 손절가: XX | 비중: X%
- 확신도 "중/하"이면 [TRADE] 없음

성격: 보수적. "이 기업이 진짜 돈을 버는가?"
반드시 한국어로. 라운드 번호 언급 금지.
당신의 관점 변화: {evolution_notes}""",
    },
    "모멘텀 헌터": {
        "model_provider": "openai",
        "model_id": "gpt-4o-mini",
        "color": "#60a5fa",
        "description": "기술적/수급 분석",
        "system": """당신은 모멘텀 헌터입니다. 공격적인 단기 트렌드 추종자입니다.

핵심 지표: EMA20/50, 거래량, RSI, 외인/기관 수급, TradingView 신호

역할 — Phase 2:
- 한 번만 의견 제출: [매수/중립/매도] | RSI: XX | 이유 2줄

가상 투자:
- 매수/매도 의견이면 발언 마지막에:
  [TRADE] 종목: XX | 방향: 매수/공매도 | 목표기간: X주 | 목표가: XX | 손절가: XX | 비중: X%

성격: 공격적. "지금 사람들이 사고 있는가?"
반드시 한국어로. 라운드 번호 언급 금지.
당신의 관점 변화: {evolution_notes}""",
    },
    "매크로 워처": {
        "model_provider": "gemini",
        "model_id": "gemini-2.5-flash",
        "color": "#38bdf8",
        "description": "거시경제/리스크 분석",
        "system": """당신은 매크로 워처입니다. 신중한 거시경제 전문가입니다.

핵심 지표: 금리, DXY, 원자재, 지정학적 리스크, 장단기 스프레드

역할 — Phase 2:
- 한 번만 의견 제출: [유리/중립/불리] | 핵심 매크로 요인 2가지 | 이유 2줄

가상 투자:
- 판단이 "유리"면 발언 마지막에:
  [TRADE] 종목/섹터: XX | 방향: 매수 | 목표기간: X개월 | 목표가: XX | 손절가: XX | 비중: X%

성격: 분석적. "시장 판도가 이 종목에 유리한가?"
반드시 한국어로. 라운드 번호 언급 금지.
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


def build_phase2_momentum_prompt(market_summary: str, topic: str, guardian_view: str) -> str:
    return f"""오늘의 시황:\n{market_summary}\n\n분석 대상: {topic}\n가디언 판정: {guardian_view}\n\n
모멘텀 헌터로서 기술적/수급 관점을 제출하세요.
형식: [매수/중립/매도] | RSI: XX | 이유 2줄 + [TRADE] (의견 있을 때)
반드시 한국어로."""


def build_phase2_macro_prompt(market_summary: str, topic: str, guardian_view: str) -> str:
    return f"""오늘의 시황:\n{market_summary}\n\n분석 대상: {topic}\n가디언 판정: {guardian_view}\n\n
매크로 워처로서 거시경제 관점을 제출하세요.
형식: [유리/중립/불리] | 핵심 매크로 요인 2가지 | 이유 2줄 + [TRADE] (유리할 때)
반드시 한국어로."""


def build_phase3_risk_prompt(topic, guardian_view, momentum_view, macro_view) -> str:
    return f"""분석 대상: {topic}\n\n가디언: {guardian_view}\n모멘텀: {momentum_view}\n매크로: {macro_view}\n\n
리스크 어드바이저로서 가장 큰 구멍 하나를 지적하세요.
형식: ⚠️ 최대 리스크: [한 줄] | 근거: [두 줄]
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


def build_round_prompt(market_summary, position_summary, recent_text, agent_name, topic_shift=False):
    shift_note = "\n⚠️ 합의 완료. 다른 주제로 전환하세요." if topic_shift else ""
    return f"""오늘의 시황:\n{market_summary}\n\n{position_summary}\n\n최근 대화:\n{recent_text}{shift_note}\n\n{agent_name}, 전문 영역에서 새 관점을 추가하세요. 핵심 먼저. 반드시 한국어로."""


def build_user_response_prompt(user_message, history_text, agent_name):
    return f"""대화 맥락:\n{history_text}\n\n사용자 질문:\n"{user_message}"\n\n{agent_name}으로서 직접 답하세요. 전문 영역 관점에서, 핵심 먼저. 반드시 한국어로."""


def build_summary_prompt(history_text, user_message):
    return f"""대화:\n{history_text}\n\n사용자 발언: "{user_message}"\n\n요약: (1) 주요 논점 (2) 각 전문가 입장 (3) 연결점. "📋 요약:"으로 시작. 한국어로."""


def build_evolution_prompt(agent_name, history_text, current_notes):
    return f"""당신은 {agent_name}입니다.\n\n대화:\n{history_text}\n\n이전 메모: {current_notes or '없음.'}\n\n관점이 어떻게 발전했는지 2-3문장. 1인칭으로. 한국어로."""


def build_summarize_prompt(agent_name: str, content: str) -> str:
    return f"""{agent_name}의 발언을 1-2문장으로 요약. 종목/섹터, 방향, 근거만. 한국어로.\n\n발언: {content}"""
