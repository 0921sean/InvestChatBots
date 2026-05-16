"""
소셜 감성 수집기 — Reddit + StockTwits + 글로벌 뉴스
텔레그램과 동일한 방식으로 봇 컨텍스트에 주입
"""
import re
import time
import random
import logging
import urllib.request
import json
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("investchat.sentiment")

_cache: dict = {}
CACHE_TTL = 1800  # 30분

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]


def _fetch_url(url: str, headers: dict = None) -> str:
    try:
        h = headers or {"User-Agent": random.choice(_USER_AGENTS)}
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug(f"fetch 오류 {url}: {e}")
        return ""


def fetch_reddit_sentiment(subreddits: list[str] = None, limit: int = 20) -> str:
    """Reddit 핫 포스트 제목 수집 (API 키 없이)."""
    if subreddits is None:
        subreddits = ["investing", "stocks", "wallstreetbets", "stockmarket", "korea"]

    cache_key = f"reddit_{','.join(subreddits)}"
    cached = _cache.get(cache_key)
    if cached and time.time() - cached["fetched_at"] < CACHE_TTL:
        return cached["text"]

    posts = []
    for sub in subreddits[:3]:  # 최대 3개 서브레딧
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
            headers = {
                "User-Agent": "InvestBot/1.0 (research purposes)",
                "Accept": "application/json",
            }
            raw = _fetch_url(url, headers)
            if not raw:
                continue
            data = json.loads(raw)
            for post in data.get("data", {}).get("children", []):
                d = post.get("data", {})
                title = d.get("title", "")
                score = d.get("score", 0)
                if title and score > 50:
                    posts.append(f"r/{sub} | {title} (👍{score})")
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            logger.debug(f"Reddit r/{sub} 오류: {e}")

    if not posts:
        return ""

    text = f"=== Reddit 감성 ({len(posts)}개 포스트) ===\n" + "\n".join(posts[:15])
    _cache[cache_key] = {"text": text, "fetched_at": time.time()}
    return text


def fetch_stocktwits_sentiment(symbol: str) -> str:
    """StockTwits 특정 종목 최신 메시지 수집."""
    cache_key = f"stocktwits_{symbol}"
    cached = _cache.get(cache_key)
    if cached and time.time() - cached["fetched_at"] < CACHE_TTL:
        return cached["text"]

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        raw = _fetch_url(url)
        if not raw:
            return ""
        data = json.loads(raw)
        messages = data.get("messages", [])
        if not messages:
            return ""

        bullish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
        bearish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
        total = len(messages)

        snippets = []
        for m in messages[:8]:
            body = m.get("body", "")[:100]
            sent = m.get("entities", {}).get("sentiment", {}).get("basic", "")
            emoji = "🟢" if sent == "Bullish" else "🔴" if sent == "Bearish" else "⚪"
            snippets.append(f"{emoji} {body}")

        text = (f"=== StockTwits ${symbol} (강세{bullish}/약세{bearish}/{total}개) ===\n"
                + "\n".join(snippets))
        _cache[cache_key] = {"text": text, "fetched_at": time.time()}
        return text
    except Exception as e:
        logger.debug(f"StockTwits {symbol} 오류: {e}")
        return ""


def fetch_news_sentiment(query: str, max_items: int = 10) -> str:
    """Google News RSS로 뉴스 헤드라인 수집."""
    cache_key = f"news_{query}"
    cached = _cache.get(cache_key)
    if cached and time.time() - cached["fetched_at"] < CACHE_TTL:
        return cached["text"]

    try:
        import urllib.parse
        encoded = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
        raw = _fetch_url(url)
        if not raw:
            return ""

        titles = re.findall(r"<title><!\[CDATA\[(.+?)\]\]></title>", raw)
        if not titles:
            titles = re.findall(r"<title>(.+?)</title>", raw)

        # 첫 번째는 피드 제목이라 제거
        titles = [t for t in titles[1:] if t and "Google" not in t][:max_items]

        if not titles:
            return ""

        text = f"=== 뉴스 감성 '{query}' ({len(titles)}개) ===\n" + "\n".join(f"• {t}" for t in titles)
        _cache[cache_key] = {"text": text, "fetched_at": time.time()}
        return text
    except Exception as e:
        logger.debug(f"뉴스 {query} 오류: {e}")
        return ""


def get_sentiment_context(stock_name: str = None, sector_name: str = None,
                           us_ticker: str = None) -> str:
    """종목/섹터 관련 전체 소셜 감성 컨텍스트 반환."""
    parts = []

    # Reddit (섹터/시장 전반 분위기)
    reddit = fetch_reddit_sentiment()
    if reddit:
        parts.append(reddit)

    # StockTwits (미국 종목만)
    if us_ticker:
        st = fetch_stocktwits_sentiment(us_ticker)
        if st:
            parts.append(st)

    # 뉴스 (한국어 + 영어)
    if stock_name:
        news_kr = fetch_news_sentiment(stock_name)
        if news_kr:
            parts.append(news_kr)
    if sector_name:
        news_sector = fetch_news_sentiment(f"{sector_name} 주식")
        if news_sector:
            parts.append(news_sector)

    return "\n\n".join(parts)
