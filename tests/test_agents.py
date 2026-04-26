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

def test_compounder_uses_claude():
    with patch("agents.anthropic.Anthropic") as MockAnth:
        instance = MockAnth.return_value
        instance.messages.create.return_value = _mock_claude("Long term is king.")
        result = call_agent("Compounder", "system", "user prompt")
    assert result == "Long term is king."

def test_razor_uses_openai():
    with patch("agents.openai.OpenAI") as MockOAI:
        instance = MockOAI.return_value
        instance.chat.completions.create.return_value = _mock_openai("RSI says buy.")
        result = call_agent("Razor", "system", "user prompt")
    assert result == "RSI says buy."

def test_moonshot_uses_gemini():
    with patch("agents.genai.GenerativeModel") as MockGemini:
        with patch("agents.genai.configure"):
            instance = MockGemini.return_value
            instance.generate_content.return_value = _mock_gemini("10x or bust.")
            result = call_agent("Moonshot", "system", "user prompt")
    assert result == "10x or bust."
