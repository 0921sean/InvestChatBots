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
    # 랜덤 2~4명 선택 구조 — 최소 2명, 최대 5명(Devil 포함 시) 발언
    with patch("orchestrator.call_agent", return_value="Test response."):
        run_round("NASDAQ dropped today.")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs if m["agent_name"] != "System"]
    known_agents = {"Compounder", "Razor", "Sigma", "Moonshot", "Tortoise", "Macro", "Devil"}
    assert len(agent_names) >= 2
    assert all(name in known_agents for name in agent_names)

def test_handle_user_message_saves_user_and_ai_messages():
    # 사용자 1 + 요약 1 + 전체 에이전트 7 = 9개 (Devil 포함 기준)
    with patch("orchestrator.call_agent", return_value="Agent reply."):
        handle_user_message("What about AI software?")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs]
    assert "User" in agent_names
    assert agent_names.count("User") == 1
    ai_msgs = [m for m in msgs if m["agent_name"] != "User"]
    assert len(ai_msgs) == 8  # 요약 1 + 에이전트 7명 모두 응답
