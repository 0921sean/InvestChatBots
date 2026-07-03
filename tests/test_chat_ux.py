"""작업 A 확장 — 채팅 답변 UX(압축·의도 라우팅·입문자 요약) 검증."""
from orchestrator import _classify_chat_intent, _build_decision_summary
from prompts import build_user_response_prompt

STOCK = "=== 삼성전자 (005930) ===\n현재가 80000\nEMA20 79000\nRSI 55"


# ── 질문 의도 라우팅 ──────────────────────────────────────
def test_intent_decision_vs_reason():
    assert _classify_chat_intent("삼성전자 살까?") == "decision"
    assert _classify_chat_intent("지금 사도 돼?") == "decision"
    assert _classify_chat_intent("인텔 물렸는데 앞으로 어떻게 될까?") == "decision"
    assert _classify_chat_intent("샌디스크 어때?") == "decision"          # 스탠스 요구 → 기본 decision
    assert _classify_chat_intent("SK하이닉스 왜 이렇게 올라?") == "reason"
    assert _classify_chat_intent("코스피 왜 플러스지?") == "reason"


# ── 프롬프트: decision은 [결정] 강제, reason은 설명(결정 금지) ──
def test_decision_prompt_forces_decision():
    p = build_user_response_prompt("삼성전자 살까?", "(없음)", "드가자",
                                   stock_context=STOCK, spoken_names=[], intent="decision")
    assert "[결정]" in p


def test_reason_prompt_explains_without_decision():
    p = build_user_response_prompt("삼성전자 왜 올라?", "(없음)", "드가자",
                                   stock_context=STOCK, spoken_names=[], intent="reason")
    assert "[결정] 표기 금지" in p or "결정을 내리지" in p
    assert "설명" in p


# ── 압축·렌즈: 짧게 + 팩트 재인용 금지 + 렌즈 차별화 ──
def test_stock_prompt_has_compression_and_lens_rules():
    for intent in ("decision", "reason"):
        p = build_user_response_prompt("삼성전자 어때?", "(없음)", "드가자",
                                       stock_context=STOCK, spoken_names=[], intent=intent)
        assert "1~2문장" in p                       # 길이 제한(압축)
        assert "다시 나열하지" in p or "반복" in p    # 팩트 재인용 금지
        assert "렌즈" in p                           # 렌즈 차별화


# ── 종합 요약 블록: 쉬운 한 줄 + 종합(카운트) + 갈린 곳 ──
def _mk(decisions):
    # decisions: list of (name, decision) → (bot_responses, parsed decisions)
    bot_responses = [(n, f"...분석...\n[결정] {d} | 이유: {n}의 렌즈 근거") for n, d in decisions]
    return bot_responses


def test_decision_summary_has_all_fields():
    bots = _mk([("드가자","관망"),("INTJ","관망"),("퀀트중독자","관망"),("빅픽처","관망"),
                ("차트천재","관망"),("기본농부","관망"),("실적왕","매수")])
    block = _build_decision_summary(bots, "삼성전자")
    assert "쉽게" in block                    # 입문자 한 줄
    assert "종합" in block                     # 종합
    assert "매수 1" in block and "관망 6" in block and "매도 0" in block
    assert "삼성전자" in block
    # 어디서 갈렸는지 — 소수의견(실적왕/매수)이 드러나야
    assert "실적왕" in block


def test_decision_summary_unanimous_notes_agreement():
    bots = _mk([(n,"매수") for n in ("드가자","INTJ","퀀트중독자","빅픽처","차트천재","기본농부","실적왕")])
    block = _build_decision_summary(bots, "엔비디아")
    assert "매수 7" in block
    assert "일치" in block                     # 만장일치 → 갈린 곳 없음 표기
