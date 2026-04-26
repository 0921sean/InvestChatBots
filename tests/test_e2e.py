import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db, _conn
init_db()

from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_db():
    yield
    with _conn() as con:
        con.executescript("""
            DELETE FROM messages;
            DELETE FROM rounds;
            DELETE FROM market_snapshots;
            DELETE FROM agent_state;
        """)


def test_get_agents():
    res = client.get("/api/agents")
    assert res.status_code == 200
    names = [a["name"] for a in res.json()]
    assert names == ["Compounder", "Razor", "Moonshot", "Tortoise"]


def test_get_messages_empty():
    res = client.get("/api/messages?since=0")
    assert res.status_code == 200
    assert res.json() == []


def test_post_user_message():
    with patch("orchestrator.call_agent", return_value="Mocked reply."):
        res = client.post("/api/user-message", json={"content": "What about semis?"})
    assert res.status_code == 200
    msgs = client.get("/api/messages?since=0").json()
    agent_names = [m["agent_name"] for m in msgs]
    assert "User" in agent_names
    assert "Compounder" in agent_names


def test_post_empty_message_rejected():
    res = client.post("/api/user-message", json={"content": "  "})
    assert res.status_code == 400


def test_start_round():
    with patch("orchestrator.call_agent", return_value="Round response."):
        with patch("orchestrator.fetch_market_data", return_value={"NASDAQ": {"symbol": "^IXIC", "price": 19800.0, "change_pct": -1.2}}):
            with patch("orchestrator.fetch_news", return_value=[{"title": "Test news", "summary": ""}]):
                res = client.post("/api/rounds/start")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    msgs = client.get("/api/messages?since=0").json()
    assert len(msgs) > 0
