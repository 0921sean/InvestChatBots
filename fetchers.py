import math
import yfinance as yf
import feedparser
from tradingview_ta import TA_Handler, Interval

TICKERS = {
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
    "KOSPI": "^KS11",
    "NVDA": "NVDA",
    "TSMC": "TSM",
}

NEWS_FEEDS = [
    # 한국
    "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258&rss=true",
    "https://www.yonhapnewstv.co.kr/category/news/economy/feed/",
    # 글로벌 (영문)
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
]

TV_SYMBOLS = {
    "NVDA":  ("NVDA",    "america", "NASDAQ"),
    "TSMC":  ("TSM",     "america", "NYSE"),
    "KOSPI": ("KOSPI",   "korea",   "KRX"),
    "BTC":   ("BTCUSDT", "crypto",  "BINANCE"),
}

def fetch_technical_analysis():
    result = {}
    for name, (symbol, screener, exchange) in TV_SYMBOLS.items():
        try:
            h = TA_Handler(symbol=symbol, screener=screener, exchange=exchange, interval=Interval.INTERVAL_1_DAY)
            a = h.get_analysis()
            result[name] = {
                "recommendation": a.summary.get("RECOMMENDATION", ""),
                "buy": a.summary.get("BUY", 0),
                "sell": a.summary.get("SELL", 0),
                "rsi": round(a.indicators.get("RSI", 0), 1),
                "macd": round(a.indicators.get("MACD.macd", 0), 2),
                "ema20": round(a.indicators.get("EMA20", 0), 1),
                "ema50": round(a.indicators.get("EMA50", 0), 1),
            }
        except Exception:
            result[name] = None
    return result

def fetch_market_data():
    result = {}
    for name, symbol in TICKERS.items():
        try:
            t = yf.Ticker(symbol)
            price = t.fast_info.last_price
            prev = t.fast_info.previous_close
            if price is None or prev is None or math.isnan(price) or math.isnan(prev) or prev == 0:
                raise ValueError("incomplete price data")
            change_pct = (price - prev) / prev * 100
            result[name] = {"symbol": symbol, "price": round(price, 2), "change_pct": round(change_pct, 2)}
        except Exception:
            result[name] = {"symbol": symbol, "price": None, "change_pct": None}
    return result

def fetch_news(max_items=10):
    seen_titles = set()
    items = []
    for url in NEWS_FEEDS:
        if len(items) >= max_items:
            break
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                if len(items) >= max_items:
                    break
                title = getattr(entry, "title", "")
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                items.append({
                    "title": title,
                    "summary": getattr(entry, "summary", ""),
                })
        except Exception:
            continue
    return items

def build_market_summary(market_data, news, ta_data=None):
    lines = ["=== 오늘의 시황 ==="]
    for name, d in market_data.items():
        if d["price"] is not None and d["change_pct"] is not None:
            arrow = "▲" if d["change_pct"] >= 0 else "▼"
            lines.append(f"{name}: {d['price']:,.2f} {arrow}{abs(d['change_pct']):.1f}%")

    if ta_data:
        lines.append("\n=== 기술적 지표 (TradingView 일봉) ===")
        for name, ta in ta_data.items():
            if ta:
                rec = ta["recommendation"].replace("STRONG_", "강한 ").replace("BUY", "매수").replace("SELL", "매도").replace("NEUTRAL", "중립")
                lines.append(f"{name}: {rec} | RSI {ta['rsi']} | MACD {ta['macd']} | EMA20 {ta['ema20']}")

    lines.append("\n=== 주요 뉴스 ===")
    for item in news[:5]:
        lines.append(f"• {item['title']}")
    return "\n".join(lines)
