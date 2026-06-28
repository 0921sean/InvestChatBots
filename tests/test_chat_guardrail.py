"""채팅 안전장치 3/3 — 봇 권유금지 프롬프트 가드레일 검증.

목표:
  G1: 채팅 응답 프롬프트(자유 질문/종목 질문 both)에 권유금지 가드레일이 강제로 포함된다.
  G2: 종목 질문([결정] 포맷)에서도 가드레일과 [결정] 안내가 공존한다.
"""
from prompts import build_user_response_prompt

# 가드레일이 반드시 담아야 할 핵심 문구들
GUARD_PHRASES = ["가상 포트폴리오", "내 가상계좌 관점", "권유", "보장", "이미 일어난 사실"]


def _prompt(stock_context=""):
    return build_user_response_prompt(
        "삼성전자 어때?", history_text="(없음)", agent_name="드가자",
        stock_context=stock_context, spoken_names=[],
    )


def test_guardrail_present_in_free_chat():
    p = _prompt(stock_context="")
    for ph in GUARD_PHRASES:
        assert ph in p, f"자유 질문 프롬프트에 '{ph}' 누락"


def test_guardrail_present_in_stock_chat():
    p = _prompt(stock_context="=== 삼성전자(005930) ===\n현재가 80000")
    for ph in GUARD_PHRASES:
        assert ph in p, f"종목 질문 프롬프트에 '{ph}' 누락"
    # [결정] 안내와 공존
    assert "[결정]" in p


def test_no_direct_recommendation_language_required():
    # "사세요/파세요" 직접 권유 금지 조항이 명시돼야 함
    p = _prompt(stock_context="")
    assert ("사세요" in p) or ("사라" in p) or ("직접 권유" in p)
