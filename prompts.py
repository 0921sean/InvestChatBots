"""
AI 투자 토론 그룹 — 7봇 섹터 라운드 시스템
드가자 / INTJ / 퀀트중독자 / 빅픽처 / 차트천재 / 기본농부 / 실적왕
"""

# 봇 페르소나·전략 프롬프트는 비공개(strategy_private.py, gitignore). 없으면 example 더미로 동작.
try:
    from strategy_private import AGENT_PROFILES, TRADING_PROFILES, _TRADE_DECISION
except ImportError:
    from strategy_private_example import AGENT_PROFILES, TRADING_PROFILES, _TRADE_DECISION

AGENT_ORDER = ["실적왕", "드가자", "INTJ", "차트천재", "역추세봇"]  # 장기 위원회 5봇(2026-07-09 개편)
# 실적왕=피터린치(펀더 주심)·드가자·INTJ=LLM 관점 / 차트천재(MACD+RSI)·역추세봇(볼린저)=규칙 기술신호
# 제거(프로필 보존, 투표 은퇴): 퀀트중독자·기본농부. 빅픽처는 역추세봇으로 개명.


# ══════════════════════════════════════════════════════════════
# 트레이딩 데스크 (서브 사이클 전용) — 공격적·단기, 별도 계좌(3천만)
# 위험·발굴 종목을 실제로 매매하기 위한 다른 성향의 봇들. 5봇 중 3표(과반) 매수.
# ══════════════════════════════════════════════════════════════

TRADING_AGENT_ORDER = ["추세질주", "테마사냥꾼", "세력추적", "바닥픽", "칼손절"]


def format_history(messages):
    return "\n".join(f"{m['agent_name']}: {m['content']}" for m in messages)


def format_history_compact(messages):
    lines = []
    for m in messages:
        text = m.get("summary") or m["content"][:120].replace("\n", " ")
        lines.append(f"{m['agent_name']}: {text}")
    return "\n".join(lines)


def discussion_etiquette(spoken_names=None) -> str:
    """봇 발언 직전 주입하는 토론 규칙.
    ① 아직 발언 안 한 봇 인용 금지(없는 발언 지어내기 방지)
    ② 앞 봇이 이미 말한 수치·근거 반복 금지 → 각자 다른 측면."""
    spoken = [n for n in (spoken_names or []) if n]
    if spoken:
        who = ", ".join(spoken)
        cite = (f"지금까지 발언한 봇은 {who} 뿐입니다. 이들만 인용·호명할 수 있고, "
                f"아직 말하지 않은 봇은 절대 언급하지 마세요. 하지 않은 발언을 지어내면 안 됩니다.")
    else:
        cite = "당신이 첫 발언자입니다. 아직 아무도 말하지 않았으니 다른 봇을 인용·호명하지 마세요."
    return (
        "[토론 규칙 — 반드시 준수]\n"
        f"- {cite}\n"
        "- 앞 봇이 이미 제시한 수치·지표(고점比 하락률·이동평균선·ROE 등)를 그대로 되풀이하지 말고, "
        "당신의 전문 영역에서 새로운 근거·다른 데이터·다른 각도를 더하세요.\n"
        "- 결론이 같아도 근거는 달라야 합니다. 앞 봇 발언을 요약·재진술하지 마세요."
    )


def build_sector_discussion_prompt(sector_name, sector_desc, market_summary, history_text,
                                    agent_name, round_num, tg_context: str = "", spoken_names=None):
    intro = f"[{sector_name} 섹터 토론 — {round_num}라운드]" if round_num > 1 else f"[{sector_name} 섹터 토론 시작]"
    tg_section = f"\n{tg_context}\n" if tg_context else ""
    return f"""{intro}
섹터: {sector_name} ({sector_desc})

시황:
{market_summary}
{tg_section}
[지금까지 대화]
{history_text or "(첫 번째 발언)"}

{discussion_etiquette(spoken_names)}

{agent_name}, {sector_name} 섹터에 대해 한 마디. 긍정/우려 핵심만 짧게.
⚠️ 개별 종목 [결정] 포맷 사용 금지. 1-2문장. 반드시 한국어."""


def build_stock_analysis_prompt(stock_data_text, sector_name, market_summary, history_text,
                                 agent_name, tg_context: str = "", portfolio_text: str = "",
                                 spoken_names=None):
    tg_section = f"\n{tg_context}\n" if tg_context else ""
    portfolio_section = f"\n[현재 포트폴리오]\n{portfolio_text}\n" if portfolio_text else ""
    return f"""[{sector_name} 섹터 — 종목 분석]

{stock_data_text}

시황 요약: {market_summary[:300]}
{tg_section}{portfolio_section}
[이전 의견]
{history_text or "(첫 번째 의견)"}

{discussion_etiquette(spoken_names)}

{agent_name}, 위 데이터를 바탕으로 투자 판단을 내려주세요.
- 본인 관점에서 핵심 근거 제시 (앞 봇과 겹치지 않는 측면으로)
- 텔레그램·블로그 여론 참고하되 맹목적으로 따르지 마세요
- **위 [현재 포트폴리오] 비중을 반드시 고려**: 이미 같은 종목·같은 섹터 비중이 큰지, 분산 관점에서 추가 매수가 합리적인지
- 반드시 [결정] 형식으로 마무리 — **마지막 줄은 정확히** `[결정] 매수|관망|매도 | 이유: <핵심근거 한 줄>` (셋 중 하나만 선택, 이 줄 없이 절대 끝내지 말 것)
2-3문장. 이미 발언한 봇에만 반응. 반드시 한국어."""


def build_holdings_review_prompt(symbol, code, entry_price, current_price, pnl_pct,
                                  days_held, buy_reasoning, stock_data_text,
                                  market_summary, history_text, agent_name):
    """월요일 보유 종목 점검 — 계속 보유(홀드) vs 매도 판단."""
    entry_fmt = f"{entry_price:,.2f}" if entry_price else "?"
    cur_fmt = f"{current_price:,.2f}" if current_price else "?"
    return f"""[보유 종목 점검 — {symbol} ({code})]

진입가 {entry_fmt} → 현재가 {cur_fmt} ({pnl_pct:+.1f}%, 보유 {days_held}일)

매수 당시 근거:
{buy_reasoning or '(기록 없음)'}

최신 데이터:
{stock_data_text}

시황 요약: {market_summary[:300]}

[다른 봇 의견]
{history_text or "(첫 번째 의견)"}

{agent_name}, 이 종목을 **계속 보유(홀드)**할지 **매도**할지 판단해주세요.
- 매수 당시 근거가 여전히 유효한가
- 현재 PnL/시황/뉴스 변화가 매도를 정당화하는가
- 추세, 손익비, 기회비용 관점에서 짧고 단호하게
- 반드시 [결정] 형식으로 마무리: **[결정] 홀드** 또는 **[결정] 매도**
1-2문장. 다른 봇 의견에 자유롭게 반응. 반드시 한국어."""


# 채팅 응답 권유금지 가드레일 (안전장치 3/3) — 모든 채팅 답변에 강제
CHAT_GUARDRAIL = """[필수 준수 — 표현 가드레일]
- 모든 매매·종목·수익 언급은 '가상 포트폴리오' 기준이다. 실제 투자 권유가 아니다.
- 사용자에게 "사세요/파세요/사라/팔아라/오릅니다" 같은 직접 권유·단정은 금지. 의견은 "내 가상계좌 관점에선 ~" 식으로만 말한다.
- 미래 예측·수익 보장 금지(오를 것이다·무조건 수익 같은 단정 금지). 이미 일어난 사실·확정 데이터에 근거해서만 말한다.
- [결정]을 쓰더라도 그건 내 가상계좌의 판단일 뿐, 사용자에게 사라/팔라는 권유가 아니다."""


def build_user_response_prompt(user_message, history_text, agent_name, stock_context: str = "",
                               spoken_names=None, intent: str = "decision"):
    stock_section = f"\n\n[자동 조회된 종목 데이터]\n{stock_context}" if stock_context else ""

    if stock_context:
        # 압축·렌즈 공통 규칙 — 팩트는 위 데이터에 이미 있으니 재인용 금지, 봇 고유 렌즈로만
        common = ("반드시 2문장 이내(초과 금지). 위 데이터의 가격·EMA·RSI·PER·PEG·EPS·성장률 같은 수치를 "
                  "그대로 다시 나열하지 말고(수치가 뭘 뜻하는지 해석만), 네 캐릭터 고유 렌즈로 본 판단만. "
                  "앞 봇이 쓴 근거는 반복하지 말고 다른 각도(차트/재무/거시/모멘텀 등 네 렌즈)로.")
        if intent == "reason":
            # '왜/이유' 질문 → 결정 강제 금지, 설명
            format_instruction = ("사용자는 '왜/이유'를 물었다. 매수·매도 결정을 내리지 말고 왜 그런지 "
                                  "네 렌즈로 설명해라. [결정] 표기 금지. " + common)
        else:
            # '살까/팔까' 등 → 스탠스 + [결정]
            format_instruction = ("먼저 네 스탠스를 밝히고, 마지막 줄은 정확히 "
                                  "`[결정] 매수|관망|매도 | 이유: <한 줄>`. " + common)
    else:
        # 자유 질문 → 각 봇 캐릭터로 자연스럽게 대화
        format_instruction = "[결정] 포맷 없이 자연스럽게 대화하세요. 1~2문장 이내. 봇 캐릭터에 맞게."

    return f"""대화 맥락:\n{history_text}{stock_section}\n\n사용자: "{user_message}"\n\n{discussion_etiquette(spoken_names)}\n\n{CHAT_GUARDRAIL}\n\n{agent_name}로서 답변. {format_instruction} 앞 봇과 다른 측면에서. 한국어."""


# 요약 전용 시스템 프롬프트 — 봇 페르소나 대신 '중립 진행자'로 라우팅(캐릭터 이탈·조언 거부 방지)
CHAT_SUMMARY_SYSTEM = (
    "너는 가상 투자 토론방의 중립 진행자다. 아래에 이미 나온 봇들의 발언을 사용자에게 "
    "짧게 요약·전달하는 것이 유일한 역할이다. 새로운 분석·전망·투자 조언을 생성하지 않는다. "
    "이건 가상 포트폴리오 토론을 구경하는 관전용 요약이며 실제 투자 권유가 아니다."
)


def build_chat_summary_prompt(user_message, answers_text):
    """자유 질문 채팅의 '한 줄 정리' — 봇 발언을 중립 요약(새 조언 생성 아님).
    (종목 매수/매도 질문은 [결정] 투표 집계로 처리하므로 여기선 자유 질문 전용)"""
    return f"""사용자 질문:\n"{user_message}"\n\n봇들이 이미 한 발언:\n{answers_text}\n\n""" + \
        "위 발언들만 근거로, 투자 입문자도 이해하게 쉬운 말로 1~2문장 요약해줘.\n" + \
        "- 위에 나온 내용만 압축할 것. 새 분석·전망·조언을 만들어내지 마.\n" + \
        "- **전문용어(EMA·RSI·PER·PEG·ROE 등) 쓰지 말고** 쉬운 일상어로 풀어써.\n" + \
        "- 의견이 갈렸으면 '봇마다 갈렸다'고 그대로 전달.\n" + \
        "- 이건 가상 토론 관전용 요약이고 투자 권유가 아니야.\n" + \
        "요약 문장만 출력(머리말·따옴표·자기소개 없이). 한국어."


def build_feedback_prompt(stock_name, pnl, pnl_pct, history_summary):
    direction = "이익" if pnl >= 0 else "손실"
    return f"""[투자 피드백]
종목: {stock_name}
결과: {direction} {abs(pnl):,.0f}원 ({pnl_pct:+.1f}%)

당시 토론 내용:
{history_summary}

이 투자 결과를 바탕으로 앞으로의 투자 방향에 대해 한 마디씩 해주세요.
무엇을 배웠고, 다음에는 어떻게 하면 좋을지. 2문장 이내. 한국어."""


def build_summarize_prompt(agent_name, content):
    return f"""{agent_name} 발언 1문장 요약. 종목+방향+근거. 한국어.\n발언: {content}"""
