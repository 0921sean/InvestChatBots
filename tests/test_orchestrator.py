import os
import pytest
from unittest.mock import patch
from db import init_db, get_messages_since
from orchestrator import run_round, handle_user_message

TEST_DB = "test_orch.db"

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_run_round_saves_agent_messages():
    with patch("orchestrator.call_agent", return_value="Test response."):
        with patch("orchestrator.fetch_technical_analysis", return_value={}):
            run_round("오늘 반도체 섹터 조정 중.")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs if m["agent_name"] != "System"]
    known = {"펀더멘털 가디언", "모멘텀 헌터", "매크로 워처", "리스크 어드바이저"}
    assert len(agent_names) >= 2
    assert all(n in known for n in agent_names)

def test_handle_user_message_saves_user_and_ai_messages():
    # 사용자 1 + 요약 1 + 에이전트 4명 = 6개
    with patch("orchestrator.call_agent", return_value="Agent reply."):
        handle_user_message("반도체 어때?")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs]
    assert "User" in agent_names
    assert agent_names.count("User") == 1
    ai_msgs = [m for m in msgs if m["agent_name"] != "User"]
    assert len(ai_msgs) >= 4  # 요약 1 + 에이전트 4명
