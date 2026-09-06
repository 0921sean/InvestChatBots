"""장 마감 중 새로 산 종목의 현재가가 안 뜨던 문제 — 회귀 테스트.

시세는 스케줄러 잡이 아니라 HTTP 요청이 올 때 **TTL이 만료돼 있어야만** 갱신된다.
장 마감 중 TTL은 30분이라, 그 사이 매수한 종목은 다음 만료까지 계속 '—'였다.
(시세 조회 자체는 마감 중에도 종가를 돌려준다 — 조회를 안 한 게 원인.)
"""
import os
from unittest.mock import patch

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db

init_db()

import main


def _reset(cache, cache_at, miss_at=0.0):
    main._price_cache = cache
    main._price_cache_at = cache_at
    main._price_miss_at = miss_at


def test_missing_symbol_triggers_refresh_even_when_ttl_is_fresh():
    """갓 매수한 종목(캐시에 없음)이 있으면 TTL이 안 지났어도 갱신한다."""
    with patch.object(main, "_price_cache_ttl", return_value=1800), \
         patch.object(main.threading, "Thread") as thread:
        _reset({"기존종목": 100.0}, main._time_module.time())   # TTL 한참 남음
        main._ensure_prices([
            {"status": "open", "symbol": "기존종목"},
            {"status": "open", "symbol": "갓산종목"},           # 캐시에 없음
        ])
        assert thread.called, "새로 산 종목이 있는데 갱신이 안 돌았다"


def test_no_refresh_when_everything_is_cached():
    """전부 캐시에 있으면 TTL 만료 전엔 외부 조회를 하지 않는다(레이트리밋 보호)."""
    with patch.object(main, "_price_cache_ttl", return_value=1800), \
         patch.object(main.threading, "Thread") as thread:
        _reset({"기존종목": 100.0}, main._time_module.time())
        main._ensure_prices([{"status": "open", "symbol": "기존종목"}])
        assert not thread.called


def test_closed_position_missing_from_cache_does_not_trigger():
    """청산된 포지션은 현재가가 필요 없다 — 이것 때문에 갱신이 돌면 안 된다."""
    with patch.object(main, "_price_cache_ttl", return_value=1800), \
         patch.object(main.threading, "Thread") as thread:
        _reset({}, main._time_module.time())
        main._ensure_prices([{"status": "closed", "symbol": "판종목"}])
        assert not thread.called


def test_repeated_misses_are_rate_limited():
    """조회가 계속 실패하는 종목이 있어도 매 요청마다 외부 API를 때리지 않는다."""
    with patch.object(main, "_price_cache_ttl", return_value=1800), \
         patch.object(main.threading, "Thread") as thread:
        _reset({}, main._time_module.time())
        rows = [{"status": "open", "symbol": "안잡히는종목"}]
        main._ensure_prices(rows)
        assert thread.call_count == 1
        for _ in range(5):                       # 폴링이 계속 들어와도
            main._ensure_prices(rows)
        assert thread.call_count == 1, "쿨다운이 안 걸려 외부 API를 폭주시킨다"


def test_expired_ttl_still_refreshes():
    """기존 동작(TTL 만료 시 갱신)이 살아 있는지."""
    with patch.object(main, "_price_cache_ttl", return_value=180), \
         patch.object(main.threading, "Thread") as thread:
        _reset({"기존종목": 100.0}, main._time_module.time() - 10_000)
        main._ensure_prices([{"status": "open", "symbol": "기존종목"}])
        assert thread.called
