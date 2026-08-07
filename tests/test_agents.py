from unittest.mock import patch, MagicMock
from agents import call_agent

def _mock_claude(text):
    mock = MagicMock()
    mock.content = [MagicMock(text=text)]
    return mock

def _mock_openai(text):
    mock = MagicMock()
    mock.choices = [MagicMock(message=MagicMock(content=text))]
    return mock

def _mock_gemini(text):
    mock = MagicMock()
    mock.text = text
    return mock





def test_claude_provider_routes_to_cli(monkeypatch):
    """현행 구조: claude provider는 SDK가 아니라 claude CLI subprocess를 경유한다."""
    import agents as agents_mod
    monkeypatch.setattr(agents_mod, "_call_claude_cli", lambda *a, **k: "CLI 응답")
    assert call_agent("드가자", "system", "user prompt") == "CLI 응답"


def test_unknown_provider_raises():
    import pytest
    from unittest.mock import patch as _patch
    fake_profiles = {
        "Ghost": {
            "model_provider": "unknown_llm",
            "model_id": "ghost-1",
            "color": "#fff",
            "description": "test",
            "system": "test",
        }
    }
    with _patch("agents.AGENT_PROFILES", fake_profiles):
        with pytest.raises(ValueError, match="Unknown provider"):
            call_agent("Ghost", "system", "user prompt")


def test_trim_false_preserves_decision_line(monkeypatch):
    """종목 [결정]처럼 끝줄이 중요한 응답은 trim=False로 넘겨 잘림 방지 (관전 피드 끊김 수정)."""
    import agents as agents_mod
    long_body = "엔비디아는 AI 가속기 시장을 사실상 독점하고 있으며 데이터센터 매출이 폭발적으로 성장하고 있다. " * 8  # >350자
    assert len(long_body) > 350
    resp = long_body + "\n[결정] 매수 | 이유: 독점적 해자"
    monkeypatch.setattr(agents_mod, "_call_claude_cli", lambda *a, **k: resp)
    # 기본(trim=True): 350자 이후 문장끝에서 잘려 [결정] 유실
    assert "[결정]" not in call_agent("드가자", "s", "u")
    # trim=False: 원문 보존 → [결정] 생존
    assert "[결정] 매수" in call_agent("드가자", "s", "u", trim=False)
