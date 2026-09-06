"""공개 API가 비공개 전략을 흘리지 않는지 — 회귀 테스트.

배경: /api/agents가 strategy_private.py의 system 프롬프트 전문을 무인증으로
서빙하고 있었다(볼밴 20·2 복귀진입, MACD GC + RSI 50, PEG<1 …).
UI는 그 필드를 오너 모달에서만 쓰는데 payload는 전원에게 나갔다.
/api/positions·/api/portfolio도 SELECT * 그대로라 strategy·stop_price가 나갔고,
stop_price + opened_at + 공개 OHLC면 비공개 상수가 거래 한 건으로 역산된다.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db, _conn

init_db()

from main import app, COOKIE_NAME, OWNER_TOKEN, _PRIVATE_POSITION_FIELDS

client = TestClient(app)


@pytest.fixture
def open_position():
    """비공개 필드가 채워진 열린 포지션 1건 — 없으면 유출 테스트가 헛돈다."""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO virtual_positions
               (symbol, code, direction, entry_price, quantity, amount, reasoning,
                status, opened_at, account, market, strategy, stop_price, half_exited)
               VALUES ('TESTCO','TESTCO','long',100.0,10,1000.0,'테스트',
                       'open','2026-08-31 00:00:00','main','US','momentum',92.5,0)""")
        pos_id = cur.lastrowid
    yield pos_id
    with _conn() as con:
        con.execute("DELETE FROM virtual_positions WHERE id=?", (pos_id,))


def test_agents_hides_system_prompt_from_visitors():
    res = client.get("/api/agents")
    assert res.status_code == 200
    agents = res.json()
    assert agents, "봇 목록이 비어 테스트가 헛돈다"
    for a in agents:
        assert "system" not in a, f"{a['name']}의 system 프롬프트가 방문자에게 노출됐다"


def test_agents_still_gives_system_prompt_to_owner():
    """오너 봇 성격 모달은 계속 동작해야 한다."""
    res = client.get("/api/agents", cookies={COOKIE_NAME: OWNER_TOKEN})
    assert res.status_code == 200
    assert all("system" in a for a in res.json())


def test_agents_keeps_visitor_facing_fields():
    """관전 UI가 쓰는 필드까지 같이 잘라먹지 않았는지."""
    a = client.get("/api/agents").json()[0]
    for field in ("name", "description", "color", "model_provider", "model_label", "desk"):
        assert field in a


def test_positions_endpoints_drop_private_fields(open_position):
    for path in ("/api/positions", "/api/portfolio"):
        res = client.get(path)
        assert res.status_code == 200
        body = res.json()
        rows = body if isinstance(body, list) else (
            (body.get("positions") or []) + ((body.get("sub") or {}).get("positions") or [])
        )
        assert any(p.get("id") == open_position for p in rows), f"{path}에 테스트 포지션이 안 실렸다"
        for p in rows:
            for f in _PRIVATE_POSITION_FIELDS:
                assert f not in p, f"{path}가 {f}를 노출한다"


def test_fund_report_drops_sourcing_lane():
    res = client.get("/api/fund-report")
    assert res.status_code == 200
    rows = res.json()
    assert rows, "리포트가 비어 테스트가 헛돈다"
    for r in rows:
        assert "desk" not in r, "소싱 레인(desk)이 노출돼 시드 목록이 재구성된다"


def test_message_page_size_is_capped():
    """limit을 크게 줘도 한 방에 전체 아카이브가 나오면 안 된다."""
    from main import _MAX_MESSAGE_PAGE

    assert len(client.get("/api/messages?since=0&limit=999999999").json()) <= _MAX_MESSAGE_PAGE
    assert len(client.get("/api/messages/latest?n=999999999").json()) <= _MAX_MESSAGE_PAGE
