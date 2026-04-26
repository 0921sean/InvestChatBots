import pytest
import os
from db import init_db, save_message, get_messages_since, save_agent_state, get_agent_state

TEST_DB = "test_investchat.db"

@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_save_and_retrieve_message():
    save_message(round_id=1, agent_name="Razor", model="gpt-4o", content="Buy the dip.")
    msgs = get_messages_since(0)
    assert len(msgs) == 1
    assert msgs[0]["agent_name"] == "Razor"
    assert msgs[0]["content"] == "Buy the dip."

def test_get_messages_since_filters_correctly():
    save_message(round_id=1, agent_name="Compounder", model="claude", content="Hold long.")
    save_message(round_id=1, agent_name="Razor", model="gpt-4o", content="Buy now.")
    msgs = get_messages_since(0)
    first_id = msgs[0]["id"]
    later = get_messages_since(first_id)
    assert len(later) == 1
    assert later[0]["agent_name"] == "Razor"

def test_agent_state_roundtrip():
    save_agent_state("Compounder", "Still bullish on semis after round 5.", 5)
    state = get_agent_state("Compounder")
    assert state["evolution_notes"] == "Still bullish on semis after round 5."
    assert state["round_count"] == 5
