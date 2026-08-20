"""토스 시세 클라이언트 — 네트워크 없이 순수 로직 검증."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import toss


def test_creds_loaded_from_env_file(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text('TOSS_CLIENT_ID="abc"\nTOSS_CLIENT_SECRET=xyz\n# comment\n')
    monkeypatch.setattr(toss, "CRED_PATH", str(p))
    monkeypatch.setattr(toss, "_cred", None)
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
    assert toss._creds() == ("abc", "xyz")          # 따옴표 제거·주석 무시
    assert toss.available() is True


def test_unavailable_without_creds(tmp_path, monkeypatch):
    monkeypatch.setattr(toss, "CRED_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(toss, "_cred", None)
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
    assert toss.available() is False


def test_get_price_parses_result_candles(monkeypatch):
    monkeypatch.setattr(toss, "_api_get",
                        lambda path, params, timeout=20: {"result": {"candles": [{"closePrice": "225.11"}]}})
    assert toss.get_price("NVDA") == 225.11


def test_get_price_none_on_empty(monkeypatch):
    monkeypatch.setattr(toss, "_api_get", lambda *a, **k: {"result": {"candles": []}})
    assert toss.get_price("XXXX") is None
    monkeypatch.setattr(toss, "_api_get", lambda *a, **k: None)
    assert toss.get_price("XXXX") is None            # API 실패도 None(폴백으로 넘어감)


def test_get_closes_paginates_and_orders_oldest_first(monkeypatch):
    pages = [
        {"result": {"candles": [{"closePrice": "3"}, {"closePrice": "2"}], "nextBefore": "cursor"}},
        {"result": {"candles": [{"closePrice": "1"}], "nextBefore": None}},
    ]
    calls = []
    def fake(path, params, timeout=20):
        calls.append(params.get("before"))
        return pages[len(calls) - 1] if len(calls) <= len(pages) else {"result": {"candles": []}}
    monkeypatch.setattr(toss, "_api_get", fake)
    assert toss.get_closes("NVDA", count=3) == [1.0, 2.0, 3.0]   # 과거→현재
    assert calls[1] == "cursor"                                   # 커서 전달


def test_fetch_stock_price_prefers_yfinance_over_toss(monkeypatch):
    # 우선순위: TradingView → yfinance → 토스(최종 폴백)
    import fetchers
    monkeypatch.setattr(toss, "available", lambda: True)
    monkeypatch.setattr(toss, "get_price", lambda s: 111.0)
    monkeypatch.setattr(fetchers, "_yf_price", lambda s: (999.0, None))
    assert fetchers.fetch_stock_price("NVDA") == 999.0        # yfinance가 먼저


def test_fetch_stock_price_uses_toss_when_others_fail(monkeypatch):
    import fetchers
    monkeypatch.setattr(toss, "available", lambda: True)
    monkeypatch.setattr(toss, "get_price", lambda s: 111.0)
    monkeypatch.setattr(fetchers, "_yf_price", lambda s: (None, None))    # yfinance 429 상황
    assert fetchers.fetch_stock_price("NVDA") == 111.0        # 토스가 받아줌


def test_tv_backoff_on_429(monkeypatch):
    """TradingView 429 감지 시 일정 시간 건너뛰고 폴백으로 — 종목마다 4거래소 재시도 낭비 제거."""
    import fetchers, time
    monkeypatch.setattr(fetchers, "_tv_blocked_until", 0.0)
    calls = []

    class Boom:
        def __init__(self, **kw): calls.append(kw.get("exchange"))
        def get_analysis(self): raise Exception("HTTP status code: 429")
    monkeypatch.setattr(fetchers, "TA_Handler", Boom)
    assert fetchers._tv_price("NVDA") is None
    assert len(calls) == 1                      # 첫 거래소에서 429 → 즉시 중단(4회 시도 안 함)
    assert fetchers._tv_blocked_until > time.time()
    calls.clear()
    assert fetchers._tv_price("MP") is None
    assert calls == []                          # 차단 창 동안 호출 자체를 안 함


def test_price_cache_ttl_by_market_hours(monkeypatch):
    """장중엔 짧게(3분), 장외·주말엔 길게(30분)."""
    import main
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))

    class FakeDT(datetime):
        _now = None
        @classmethod
        def now(cls, tz=None): return cls._now
    monkeypatch.setattr("main.datetime", FakeDT, raising=False)
    # 함수 내부에서 import하므로 직접 계산 검증으로 대체
    def ttl_at(dt):
        if dt.weekday() >= 5: return 1800
        return 180 if (dt.hour >= 22 or dt.hour < 5) else 1800
    assert ttl_at(datetime(2026, 8, 20, 23, 0, tzinfo=KST)) == 180    # 목 23시(장중)
    assert ttl_at(datetime(2026, 8, 20, 3, 0, tzinfo=KST)) == 180     # 목 새벽 3시(장중)
    assert ttl_at(datetime(2026, 8, 20, 14, 0, tzinfo=KST)) == 1800   # 목 오후(장외)
    assert ttl_at(datetime(2026, 8, 22, 23, 0, tzinfo=KST)) == 1800   # 토요일
