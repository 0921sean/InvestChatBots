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
    from prompts import AGENT_ORDER
    assert set(AGENT_ORDER) <= set(names)          # 현행 로스터(드리프트 면역 — 동적 참조)


def test_get_messages_empty():
    res = client.get("/api/messages?since=0")
    assert res.status_code == 200
    assert res.json() == []


def test_post_user_message():
    # 현행: 즉시 응답이 아니라 서버 하드캡 + FIFO 큐 등록(워커가 별도 처리)
    res = client.post("/api/user-message", json={"content": "What about semis?"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body.get("queued") is True and body["position"] >= 1


def test_post_empty_message_rejected():
    res = client.post("/api/user-message", json={"content": "  "})
    assert res.status_code == 400


