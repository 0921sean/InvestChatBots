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


def test_ensure_fund_accounts_seeds_four_and_leaves_committee():
    from db import (ensure_fund_accounts, get_shared_portfolio,
                    FUND_ACCOUNTS, FUND_SEED, INITIAL_BALANCE)
    main_before = get_shared_portfolio("main")["balance"]
    ensure_fund_accounts()
    for acct in FUND_ACCOUNTS:                       # P/W/S/Q 각 1억
        assert get_shared_portfolio(acct)["balance"] == FUND_SEED
    assert get_shared_portfolio("main")["balance"] == main_before  # committee 무변경
    assert main_before == INITIAL_BALANCE
    ensure_fund_accounts()                           # 멱등 — 다시 불러도 그대로
    assert get_shared_portfolio("P")["balance"] == FUND_SEED
