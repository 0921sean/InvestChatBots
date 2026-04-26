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

def test_run_round_saves_four_agent_messages():
    with patch("orchestrator.call_agent", return_value="Test response."):
        run_round("NASDAQ dropped today.")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs]
    assert "Compounder" in agent_names
    assert "Razor" in agent_names
    assert "Moonshot" in agent_names
    assert "Tortoise" in agent_names

def test_handle_user_message_saves_user_and_five_ai_messages():
    with patch("orchestrator.call_agent", return_value="Agent reply."):
        handle_user_message("What about AI software?")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs]
    assert "User" in agent_names
    assert agent_names.count("User") == 1
    ai_msgs = [m for m in msgs if m["agent_name"] != "User"]
    assert len(ai_msgs) == 5  # 1 summary + 4 agent responses
