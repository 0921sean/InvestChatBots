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



# ── 운영 콘솔 통합 (2026-08-23): /owner/approvals·/owner/seeds 폐지 → /admin ──
def test_admin_serves_console_when_owner():
    import main as m
    c = TestClient(m.app)
    assert "관리자 로그인" in c.get("/admin").text          # 미인증 = 로그인 폼
    c.cookies.set(m.COOKIE_NAME, m.OWNER_TOKEN)
    body = c.get("/admin").text
    assert "운영 콘솔" in body
    for sec in ["리스크 가드", "결재 대기", "병목 시드 결재",
                "방문 통계", "피드백", "사용자 채팅 기록"]:
        assert sec in body, sec


def test_retired_owner_pages_redirect_to_admin():
    """폰에 남은 옛 알림 링크가 막다른 403이 되지 않아야 한다."""
    import main as m
    c = TestClient(m.app)
    for url in ("/owner/approvals", "/owner/seeds", "/owner/junk"):
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/admin", url
        # 틀린 토큰으로 쿠키가 발급되면 안 된다(인증 우회 방지)
        assert m.COOKIE_NAME not in r.headers.get("set-cookie", ""), url


def test_owner_token_login_still_sets_cookie():
    import main as m
    c = TestClient(m.app)
    r = c.get(f"/owner/{m.OWNER_TOKEN}", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/"
    assert m.COOKIE_NAME in r.headers.get("set-cookie", "")


def test_ntfy_approval_links_point_to_admin():
    """결재 알림 링크가 폐지된 /owner/approvals를 가리키면 안 된다."""
    src = open("aifund.py", encoding="utf-8").read()
    assert "/owner/approvals" not in src and "/owner/seeds" not in src
