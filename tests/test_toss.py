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


def test_fetch_stock_price_prefers_toss(monkeypatch):
    import fetchers
    monkeypatch.setattr(toss, "available", lambda: True)
    monkeypatch.setattr(toss, "get_price", lambda s: 111.0)
    monkeypatch.setattr(fetchers, "_yf_price", lambda s: (999.0, None))   # 폴백은 안 쓰여야
    assert fetchers.fetch_stock_price("NVDA") == 111.0


def test_fetch_stock_price_falls_back_when_toss_fails(monkeypatch):
    import fetchers
    monkeypatch.setattr(toss, "available", lambda: True)
    monkeypatch.setattr(toss, "get_price", lambda s: None)               # 토스 실패
    monkeypatch.setattr(fetchers, "_yf_price", lambda s: (999.0, None))
    assert fetchers.fetch_stock_price("NVDA") == 999.0                   # yfinance 폴백
