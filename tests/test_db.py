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

def test_desk_separates_committee_and_fund_feeds():
    from db import get_recent_messages
    save_message(round_id=1, agent_name="드가자", model="claude", content="committee 발언")   # desk=None
    save_message(round_id=None, agent_name="피터 린치", model="sonnet", content="fund 발언", desk="fund")
    # 기본(committee) 피드는 fund 내레이션을 안 본다
    committee = get_messages_since(0)
    assert [m["agent_name"] for m in committee] == ["드가자"]
    # fund 피드는 fund 내레이션만 본다
    fund = get_messages_since(0, desk="fund")
    assert [m["agent_name"] for m in fund] == ["피터 린치"]
    # 최근 N개도 동일하게 분리
    assert [m["agent_name"] for m in get_recent_messages(10)] == ["드가자"]
    assert [m["agent_name"] for m in get_recent_messages(10, desk="fund")] == ["피터 린치"]


def test_narrate_writes_fund_desk_only():
    import aifund
    aifund._narrate("P", "관망하겠습니다.", model="sonnet")
    assert get_messages_since(0) == []                          # committee 피드 오염 없음
    fund = get_messages_since(0, desk="fund")
    # 전략 노출 방지 — 발화자는 알파벳 한 글자
    assert len(fund) == 1 and fund[0]["agent_name"] == "P"


def test_fund_nav_snapshot_roundtrip_and_upsert():
    from db import record_fund_nav, get_fund_nav_history
    record_fund_nav("P", "2026-07-30", 100_000_000)
    record_fund_nav("P", "2026-07-31", 101_000_000)
    record_fund_nav("P", "2026-07-31", 102_000_000)   # 같은 날 재기록 → 갱신(중복 X)
    hist = get_fund_nav_history("P")
    assert [h["equity"] for h in hist] == [100_000_000, 102_000_000]   # 날짜 오름차순, upsert
    assert get_fund_nav_history("W") == []            # 봇별 분리


def test_fund_report_roundtrip_and_upsert():
    from db import record_fund_report, get_fund_reports
    record_fund_report("2026-07-31", "NVDA", "NVIDIA", "GPU 만드는 회사", "PER 50 · 성장 +60%", "PW")
    record_fund_report("2026-07-31", "COHR", "Coherent", "광부품 병목", "마진 30%", "S")
    record_fund_report("2026-07-31", "NVDA", "NVIDIA", "AI 가속기 대장", "PER 48", "PW")  # 갱신
    rows = get_fund_reports()
    by_code = {r["code"]: r for r in rows}
    assert by_code["NVDA"]["summary"] == "AI 가속기 대장"   # upsert(중복 X)
    assert by_code["COHR"]["desk"] == "S"                    # 병목 자체소싱 태그
    assert len(rows) == 2


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
