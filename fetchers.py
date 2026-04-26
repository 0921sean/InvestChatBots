import yfinance as yf
import feedparser

TICKERS = {
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
    "KOSPI": "^KS11",
    "NVDA": "NVDA",
    "TSMC": "TSM",
}

NEWS_FEEDS = [
    "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258&rss=true",
    "https://www.yonhapnewstv.co.kr/category/news/economy/feed/",
]

def fetch_market_data():
    result = {}
    for name, symbol in TICKERS.items():
        try:
            t = yf.Ticker(symbol)
            price = t.fast_info.last_price
            prev = t.fast_info.previous_close
            change_pct = ((price - prev) / prev * 100) if prev else 0.0
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

def build_market_summary(market_data, news):
    lines = ["=== Today's Market ==="]
    for name, d in market_data.items():
        if d["price"]:
            arrow = "▲" if d["change_pct"] >= 0 else "▼"
            lines.append(f"{name}: {d['price']:,.2f} {arrow}{abs(d['change_pct']):.1f}%")
    lines.append("\n=== Top News ===")
    for item in news[:5]:
        lines.append(f"• {item['title']}")
    return "\n".join(lines)
