import pytest
from unittest.mock import patch, MagicMock
from fetchers import fetch_market_data, fetch_news, build_market_summary

def test_fetch_market_data_returns_dict():
    mock_ticker = MagicMock()
    mock_ticker.fast_info.last_price = 19800.0
    mock_ticker.fast_info.previous_close = 20000.0
    with patch("fetchers.yf.Ticker", return_value=mock_ticker):
        result = fetch_market_data()
    assert "NASDAQ" in result
    assert result["NASDAQ"]["price"] == 19800.0
    assert result["NASDAQ"]["change_pct"] == pytest.approx(-1.0, abs=0.01)

def test_fetch_news_returns_list():
    mock_feed = MagicMock()
    mock_feed.entries = [
        MagicMock(title="반도체 급락", summary="삼성전자 3% 하락"),
        MagicMock(title="AI 투자 지속", summary="빅테크 AI 지출 확대"),
    ]
    with patch("fetchers.feedparser.parse", return_value=mock_feed):
        result = fetch_news()
    assert len(result) == 2
    assert result[0]["title"] == "반도체 급락"

def test_build_market_summary_contains_ticker_names():
    market_data = {
        "NASDAQ": {"symbol": "^IXIC", "price": 19800.0, "change_pct": -1.2},
        "KOSPI": {"symbol": "^KS11", "price": 2550.0, "change_pct": 0.3},
    }
    news = [{"title": "반도체 급락", "summary": ""}]
    summary = build_market_summary(market_data, news)
    assert "NASDAQ" in summary
    # 현행 포맷: KR '지수'는 미표시(US-only 피벗) — 국내는 개별 종목명만 노출
    assert "KOSPI" not in summary
    assert "반도체 급락" in summary
