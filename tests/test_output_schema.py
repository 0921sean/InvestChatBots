"""작업 A — LLM 출력 스키마 강제(앱단) 어드버서리얼 검증.

모델이 빈/엉뚱한 출력을 줘도:
  G1) 섹터 합의 4필드(방향=outlook, 결론=decision, 핵심이유=thesis, 봇_한마디=spokesperson+quote)가
      반드시 유효/비지 않게 채워진다. '-'/'—'/빈값 금지.
  G2) 모든 종목 출력에 decision enum(매수|관망|매도) + 근거가 반드시 존재한다.
      "결정 없이 텍스트만" 끝이 구조적으로 불가능.
"""
import re
import pytest
from orchestrator import _enforce_consensus_schema, _ensure_decision_line, _parse_decision

OUTLOOKS = {"긍정", "중립", "부정"}
DECISIONS = {"매수", "관망", "매도"}
EMPTY = {"", "-", "—", "–", "…", "..."}


# ── G1: 섹터 합의 ─────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    {},
    None,
    {"outlook": "몰라", "decision": "", "thesis": "-", "spokesperson": "", "quote": "—"},
    {"outlook": "중립(부정 우위)", "decision": "사자", "thesis": "   ", "spokesperson": "없는봇", "quote": ""},
    {"thesis": None, "spokesperson": None, "quote": None},
    "완전히 깨진 문자열",  # dict 아님
])
def test_consensus_four_fields_always_filled(bad):
    d = _enforce_consensus_schema(bad, "반도체")
    assert d["outlook"] in OUTLOOKS
    assert d["decision"] in DECISIONS
    for f in ("thesis", "spokesperson", "quote"):
        assert d[f] and d[f].strip() not in EMPTY, f"{f} 비었음: {d[f]!r}"


def test_consensus_thesis_backfilled_from_drivers():
    d = _enforce_consensus_schema({"thesis": "-", "drivers": ["AI 수요 급증", "HBM 단가 상승"]}, "반도체")
    assert "AI 수요 급증" in d["thesis"]


def test_consensus_keeps_valid_values():
    src = {"outlook": "긍정", "decision": "매수", "thesis": "좋다", "spokesperson": "INTJ", "quote": "담는다"}
    d = _enforce_consensus_schema(src, "반도체")
    assert (d["outlook"], d["decision"], d["thesis"], d["spokesperson"], d["quote"]) == \
           ("긍정", "매수", "좋다", "INTJ", "담는다")


def test_consensus_uses_fallback_quote_when_given():
    d = _enforce_consensus_schema({"thesis": "x"}, "반도체",
                                  fallback_sp="차트천재", fallback_qt="추세 살아있다")
    assert d["spokesperson"] == "차트천재"
    assert d["quote"] == "추세 살아있다"


# ── G2: 종목별 결정 ───────────────────────────────────────
@pytest.mark.parametrize("text", [
    "",
    None,
    "그냥 분석만 하고 결정은 안 적었다",
    "RSI 54.6, EMA20 위. 괜찮아 보인다.",
    "한 줄짜리",
])
def test_decision_always_present(text):
    out = _ensure_decision_line(text)
    assert re.search(r'\[결정\]\s*(매수|관망|매도)', out), f"결정 누락: {out!r}"
    assert "이유:" in out
    dec, _ = _parse_decision(out)
    assert dec in DECISIONS


def test_decision_preserved_when_present():
    t = "삼성전자 분석...\n[결정] 매수 | 이유: ROE 18.9% 우량주"
    assert _ensure_decision_line(t) == t          # 이미 있으면 그대로
    assert _parse_decision(t)[0] == "매수"


def test_decision_default_is_conservative_hold():
    # 결정 없는 텍스트 → 보수적 관망 (스푸리어스 매수 방지)
    out = _ensure_decision_line("강력 추천! 무조건 좋다")
    assert _parse_decision(out)[0] == "관망"


# ── 통합: 라이브 합의 경로가 빈 모델 출력에도 4필드 렌더 ──
def test_consensus_live_path_renders_all_fields_on_empty_model(monkeypatch, tmp_path):
    import orchestrator as orch
    from db import init_db, get_messages_since
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    init_db()
    monkeypatch.setattr(orch, "call_agent", lambda *a, **k: "")  # 모델이 빈 출력
    sector = {"name": "반도체", "stocks": [{"name": "삼성전자"}]}
    orch._extract_sector_consensus(1, sector, "시장 요약")
    sysmsgs = [m["content"] for m in get_messages_since(0)
               if m["agent_name"] == "System" and "섹터 토론 완료" in m["content"]]
    assert sysmsgs, "합의 System 메시지가 없음"
    txt = sysmsgs[0]
    assert all(k in txt for k in ("방향:", "결론:", "핵심:", "한마디:"))
    assert "핵심: —" not in txt and "핵심: -" not in txt and "핵심: \n" not in txt
