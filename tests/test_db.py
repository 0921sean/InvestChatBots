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


def test_ensure_desk_accounts_seeds_5000_and_leaves_committee():
    from db import (ensure_desk_accounts, get_shared_portfolio, DESK_ACCOUNTS,
                    DESK_SEED, INITIAL_BALANCE)
    main_before = get_shared_portfolio("main")["balance"]
    ensure_desk_accounts()
    for acct in DESK_ACCOUNTS:                          # 대형주/발굴주 각 5,000만
        assert get_shared_portfolio(acct)["balance"] == DESK_SEED
    assert get_shared_portfolio("main")["balance"] == main_before   # committee 무영향
    ensure_desk_accounts()                              # 멱등(중복 시드 안 함)
    assert get_shared_portfolio("대형주")["balance"] == DESK_SEED


def test_largecap_watch_crud():
    from db import add_to_watch, get_watchlist, mark_watch
    add_to_watch("NVDA", "엔비디아", "P,H")
    add_to_watch("TSM", "TSMC", "W")
    assert {w["code"] for w in get_watchlist()} == {"NVDA", "TSM"}    # 진입대기
    mark_watch("NVDA", "bought")
    assert {w["code"] for w in get_watchlist()} == {"TSM"}            # bought는 대기서 빠짐
    assert {w["code"] for w in get_watchlist("bought")} == {"NVDA"}
    add_to_watch("NVDA", "엔비디아", "S")                              # bought는 재관심에도 유지
    assert get_watchlist("bought")[0]["code"] == "NVDA"


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


def test_bottleneck_seed_add_approve_and_read():
    from db import (add_bottleneck_seed, get_bottleneck_seeds,
                    get_bottleneck_seed_rows, decide_bottleneck_seed)
    # 신규 후보는 pending — approved 목록에 안 뜬다
    assert add_bottleneck_seed("axti", "InP 상류 기판 초크포인트", source="agent") is True
    assert add_bottleneck_seed("AXTI", "중복") is False               # 멱등(대소문자 정규화)
    assert get_bottleneck_seeds("approved") == []
    assert get_bottleneck_seeds("pending") == ["AXTI"]
    # 승인 → approved 목록에 등장
    assert decide_bottleneck_seed("AXTI", "approved") is True
    assert get_bottleneck_seeds("approved") == ["AXTI"]
    assert get_bottleneck_seeds("pending") == []
    # 반려는 approved에서 빠진다
    add_bottleneck_seed("JUNK", "참치")
    decide_bottleneck_seed("JUNK", "rejected")
    assert get_bottleneck_seeds("approved") == ["AXTI"]
    assert {r["ticker"] for r in get_bottleneck_seed_rows()} == {"AXTI", "JUNK"}


def test_bottleneck_backfill_once_and_preserves_curation():
    from db import (backfill_bottleneck_seeds, get_bottleneck_seeds,
                    add_bottleneck_seed)
    # 빈 테이블 → 하드코딩 시드를 approved로 백필
    assert backfill_bottleneck_seeds(["COHR", "LITE"]) == 2
    assert set(get_bottleneck_seeds("approved")) == {"COHR", "LITE"}
    # 이미 시드가 있으면 재백필 안 함(사람 큐레이션 보존)
    add_bottleneck_seed("MU")                                        # pending 하나 추가
    assert backfill_bottleneck_seeds(["NVDA", "AVGO"]) == 0
    assert "NVDA" not in get_bottleneck_seeds(None)


def test_pending_buy_add_dedup_and_decide():
    from db import (add_pending_buy, has_pending_buy, get_pending_buys,
                    get_pending_buy, decide_pending_buy)
    pid = add_pending_buy("Veeva", "VEEV", "발굴주", "발굴주", "US", 2_500_000, 240.5,
                          "P,W", stock_desc="수직 SaaS", reason="PEG 매력", q_comment=None)
    assert pid > 0 and has_pending_buy("VEEV", "발굴주")
    assert add_pending_buy("Veeva", "VEEV", "발굴주", "발굴주", "US", 2_500_000, 999, "P") == 0  # 중복 대기 skip
    assert [r["code"] for r in get_pending_buys("pending")] == ["VEEV"]
    # 승인 → 행 반환(체결 실행용), pending에서 빠짐
    row = decide_pending_buy(pid, "approved")
    assert row["decision_price"] == 240.5 and row["status"] == "approved"
    assert get_pending_buys("pending") == []
    assert decide_pending_buy(pid, "approved") is None            # 이미 처리됨
    assert not has_pending_buy("VEEV", "발굴주")                   # 재상신 가능
