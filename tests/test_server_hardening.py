"""서버 하드닝 회귀 테스트 — 감사 1회차 P1 항목.

셋 다 "값이 없으면 통과"류의 fail-open이거나 방어가 없던 자리다.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db, _conn, feedback_rate_limited

init_db()

import main
from main import app

client = TestClient(app)


# ── 피드백 레이트리밋: 쿠키를 안 보내면 무제한이던 문제 ──────────────
def test_rate_limit_fails_closed_without_visitor_id():
    """옛 코드는 visitor_id가 없으면 False(통과)를 반환했다 — 쿠키만 빼면 무제한이었다."""
    assert feedback_rate_limited(None) is True
    assert feedback_rate_limited("") is True


def test_rate_limit_still_counts_normally_with_id():
    with _conn() as con:
        con.execute("DELETE FROM feedback WHERE visitor_id='RL_TEST'")
    assert feedback_rate_limited("RL_TEST", window_min=5, max_n=3) is False


def test_client_key_prefers_forwarded_ip():
    """cloudflared 뒤라 X-Forwarded-For의 첫 항목이 원 클라이언트다."""
    class Req:
        headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
        client = None
    assert main._client_key(Req()) == "ip:203.0.113.9"


# ── 로그인 브루트포스 ────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clear_login_state():
    main._login_fails.clear()
    yield
    main._login_fails.clear()


def test_login_locks_out_after_repeated_failures():
    """옛 방어는 0.4초 sleep뿐 — sync 라우트라 스레드풀로 초당 ~100회가 가능했다."""
    hdr = {"x-forwarded-for": "198.51.100.7"}
    for _ in range(main._LOGIN_MAX_FAILS):
        assert client.post("/api/admin/login", json={"password": "wrong"}, headers=hdr).status_code == 403
    # 임계 초과 → 403이 아니라 429(잠금)
    res = client.post("/api/admin/login", json={"password": "wrong"}, headers=hdr)
    assert res.status_code == 429


def test_lockout_is_per_ip():
    """한 IP가 잠겼다고 다른 사용자가 막히면 안 된다."""
    a = {"x-forwarded-for": "198.51.100.8"}
    b = {"x-forwarded-for": "198.51.100.9"}
    for _ in range(main._LOGIN_MAX_FAILS):
        client.post("/api/admin/login", json={"password": "wrong"}, headers=a)
    assert client.post("/api/admin/login", json={"password": "wrong"}, headers=a).status_code == 429
    assert client.post("/api/admin/login", json={"password": "wrong"}, headers=b).status_code == 403


# ── 채팅 메시지 길이 ─────────────────────────────────────────────
def test_user_message_length_capped():
    """질문 1건이 4~7봇 + 요약으로 팬아웃되므로 대용량 입력은 토큰 폭탄이다."""
    res = client.post("/api/user-message", json={"content": "가" * 5000})
    assert res.status_code == 422, "길이 제한이 없다"


def test_normal_user_message_still_accepted():
    """조이다가 정상 질문까지 막지 않았는지.

    큐에 실제로 쌓이므로 뒷정리한다 — 남기면 전역 일일 캡(30)을 갉아먹어
    다른 테스트가 알 수 없는 이유로 실패한다.
    """
    body = "엔비디아 어떻게 보세요?"
    res = client.post("/api/user-message", json={"content": body})
    assert res.status_code == 200
    with _conn() as con:
        con.execute("DELETE FROM chat_queue WHERE content=?", (body,))
