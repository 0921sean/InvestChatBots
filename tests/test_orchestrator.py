import os
import pytest
from unittest.mock import patch
from db import init_db, get_messages_since
from orchestrator import handle_user_message

TEST_DB = "test_orch.db"

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    # test_e2e가 main을 import하며 load_dotenv() 부작용으로 ROOT_IS_FUND가 실릴 수 있음 —
    # 이 테스트는 committee 모드(desk=None) 가정이므로 명시 제거(실행 순서 무관하게).
    monkeypatch.delenv("ROOT_IS_FUND", raising=False)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_handle_user_message_saves_user_and_ai_messages():
    # 사용자 1 + 요약 1 + 에이전트 4명 = 6개
    with patch("orchestrator.call_agent", return_value="Agent reply."):
        handle_user_message("반도체 어때?")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs]
    assert "User" in agent_names
    assert agent_names.count("User") == 1
    ai_msgs = [m for m in msgs if m["agent_name"] != "User"]
    assert len(ai_msgs) >= 4  # 요약 1 + 에이전트 4명
