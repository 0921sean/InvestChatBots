"""owner_guard 회귀 테스트 — 무인증 privileged 라우트가 다시 생기지 않게.

배경: 옛 owner_guard는 '오너 전용 라우트 허용목록'이라 새 라우트를 목록에
등록하는 걸 빠뜨리면 조용히 공개됐다. 실제로 /api/main-cycle/resume-force와
/api/sub-cycle/measure가 무인증으로 노출돼 있었다(익명이 토큰 소각·pause 해제 가능).
deny-by-default로 반전한 뒤 그 성질이 유지되는지 여기서 잠근다.
"""
import os

from fastapi.testclient import TestClient

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db

init_db()

from main import app, _MUTATING_METHODS, _PUBLIC_MUTATING_ROUTES

client = TestClient(app)


def _mutating_routes():
    """앱에 등록된 (method, path) 중 상태를 바꾸는 것 전부."""
    out = set()
    for route in app.routes:
        for method in getattr(route, "methods", set()) & _MUTATING_METHODS:
            out.add((method, route.path))
    return out


def test_every_non_public_mutating_route_returns_403_anonymously():
    """앱의 모든 mutating 라우트를 쿠키 없이 실제로 때려서 403을 확인한다.

    미들웨어가 핸들러 앞단에서 막으므로 부작용(토큰 소비·매매·DB 변경)은 없다.
    새 POST/DELETE를 가드 없이 추가하면 여기서 바로 깨진다.
    """
    targets = _mutating_routes() - _PUBLIC_MUTATING_ROUTES
    assert targets, "mutating 라우트를 하나도 못 찾았다 — 테스트가 헛돌고 있다"

    leaked = []
    for method, path in sorted(targets):
        concrete = path.replace("{id}", "1")  # 동적 경로는 임의값으로 구체화
        res = client.request(method, concrete)
        if res.status_code != 403:
            leaked.append(f"{method} {path} → {res.status_code}")
    assert not leaked, "무인증으로 뚫린 라우트: " + ", ".join(leaked)


def test_public_mutating_allowlist_is_exactly_the_intended_set():
    """공개 mutating 라우트가 늘어나면 의도적으로만 늘어나게 못박는다."""
    assert _PUBLIC_MUTATING_ROUTES == {
        ("POST", "/api/admin/login"),
        ("POST", "/api/visit"),
        ("POST", "/api/user-message"),
        ("POST", "/api/feedback"),
        ("POST", "/api/contact"),
        ("POST", "/api/note"),
    }


def test_previously_unguarded_routes_now_blocked():
    """실제로 뚫려 있던 두 라우트 — 쿠키 없이 호출하면 403이어야 한다.

    미들웨어가 핸들러 앞에서 막으므로 이 호출은 LLM/토큰을 쓰지 않는다.
    """
    for path in ("/api/main-cycle/resume-force", "/api/sub-cycle/measure"):
        res = client.post(path)
        assert res.status_code == 403, f"{path}가 무인증으로 뚫려 있다"


def test_owner_only_gets_blocked_without_cookie():
    for path in ("/api/_debug/state", "/api/_debug/jobs", "/api/lecture-notes"):
        res = client.get(path)
        assert res.status_code == 403, f"{path}가 무인증으로 뚫려 있다"


def test_public_reads_still_open():
    """관전 피드는 공개가 제품이다 — 조이다가 같이 막지 않았는지 확인."""
    for path in ("/api/state", "/api/messages?since=0", "/api/portfolio", "/api/token-usage"):
        assert client.get(path).status_code == 200, f"{path}가 방문자에게 막혔다"


def test_openapi_schema_not_exposed():
    """라우트 인벤토리 공개 = 가드 누락 라우트를 찾는 지도."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404
