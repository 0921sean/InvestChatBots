"""채팅 안전장치 1/3 — 서버단 하드 캡 + FIFO 큐 검증.

목표:
  G1 (캡)  : 그날 N개까지만 접수, (N+1)번째는 거부('오늘 마감').
  G2 (큐)  : FIFO 1건씩 처리, 대기 순번 정확.
  G3 (영속): 카운터·큐가 새 커넥션(=재시작 모사)에도 유지.
"""
import os
import pytest

from db import (
    init_db,
    enqueue_chat_question,
    claim_next_chat_question,
    complete_chat_question,
    get_chat_queue_status,
)

TEST_DB = "test_chat_queue.db"


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# ── G1: 하드 캡 ────────────────────────────────────────────
def test_cap_blocks_the_n_plus_1th():
    cap = 3
    for i in range(cap):
        res = enqueue_chat_question(f"q{i}", daily_cap=cap)
        assert res["accepted"] is True
        assert res["closed"] is False

    # (N+1)번째 = 거부
    blocked = enqueue_chat_question("overflow", daily_cap=cap)
    assert blocked["accepted"] is False
    assert blocked["closed"] is True
    assert blocked["remaining"] == 0


def test_remaining_counts_down():
    cap = 5
    r1 = enqueue_chat_question("a", daily_cap=cap)
    assert r1["remaining"] == 4
    r2 = enqueue_chat_question("b", daily_cap=cap)
    assert r2["remaining"] == 3


# ── G2: FIFO + 순번 ────────────────────────────────────────
def test_fifo_order_and_positions():
    cap = 10
    p1 = enqueue_chat_question("first", daily_cap=cap)
    p2 = enqueue_chat_question("second", daily_cap=cap)
    p3 = enqueue_chat_question("third", daily_cap=cap)
    assert [p1["position"], p2["position"], p3["position"]] == [1, 2, 3]

    # 워커가 가장 오래된 것부터 가져간다
    a = claim_next_chat_question()
    assert a["content"] == "first"
    b = claim_next_chat_question()
    assert b["content"] == "second"


def test_position_advances_after_complete():
    cap = 10
    enqueue_chat_question("first", daily_cap=cap)
    enqueue_chat_question("second", daily_cap=cap)

    claimed = claim_next_chat_question()        # first → processing (순번 1)
    st = get_chat_queue_status(daily_cap=cap)
    assert st["waiting"] == 2                    # 처리중 1 + 대기 1
    assert st["queue"][0]["position"] == 1

    complete_chat_question(claimed["id"])        # first 완료
    st2 = get_chat_queue_status(daily_cap=cap)
    assert st2["waiting"] == 1                    # second만 남음
    assert st2["queue"][0]["position"] == 1


def test_completed_question_not_reclaimed():
    enqueue_chat_question("only", daily_cap=10)
    q = claim_next_chat_question()
    complete_chat_question(q["id"])
    assert claim_next_chat_question() is None


# ── G3: 영속 ───────────────────────────────────────────────
def test_count_persists_across_connections():
    cap = 10
    enqueue_chat_question("a", daily_cap=cap)
    enqueue_chat_question("b", daily_cap=cap)
    # get_chat_queue_status는 별도 커넥션 — 재시작 후 조회를 모사
    st = get_chat_queue_status(daily_cap=cap)
    assert st["today_count"] == 2
    assert st["remaining"] == cap - 2
    assert st["closed"] is False
