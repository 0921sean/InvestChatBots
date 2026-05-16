"""
네이버 증권 종목 오픈톡 수집기
- m.stock.naver.com Next.js SSR 데이터에서 실시간 오픈톡 메시지 추출
- 인증 불필요 (공개 SSR 데이터)
- 캐시: 종목당 20분 TTL
"""
import re
import json
import time
import random
import logging
import urllib.request

logger = logging.getLogger("investchat.board")

_cache: dict = {}
CACHE_TTL = 1200  # 20분

_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

# KR 종목코드 → 네이버 모바일 URL 코드 (6자리 그대로)
# US 종목 → reutersCode 형식 필요 (TSM → TSM.N 등)
_US_REUTERS = {
    "NVDA": "NVDA.O", "AAPL": "AAPL.O", "MSFT": "MSFT.O", "META": "META.O",
    "GOOGL": "GOOGL.O", "AMZN": "AMZN.O", "TSLA": "TSLA.O", "AMD": "AMD.O",
    "TSM": "TSM.N", "AVGO": "AVGO.O", "PLTR": "PLTR.N", "ONDS": "ONDS.O",
}


def _fetch_discuss(url: str) -> list[dict]:
    """Naver 모바일 discuss 페이지 SSR에서 오픈톡 메시지 추출."""
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Referer": "https://m.stock.naver.com/",
    }
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as r:
                html = r.read().decode("utf-8", errors="ignore")

            m = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            if not m:
                return []

            data = json.loads(m.group(1))
            queries = (data.get("props", {})
                           .get("pageProps", {})
                           .get("dehydratedState", {})
                           .get("queries", []))

            for q in queries:
                result = q.get("state", {}).get("data", {}).get("result", {})
                if isinstance(result, dict) and "recentMessages" in result:
                    msgs = result.get("filteredRecentMessages") or result.get("recentMessages", [])
                    return msgs
            return []

        except Exception as e:
            logger.debug(f"discuss 수집 오류 (시도{attempt+1}): {e}")
            if attempt < 2:
                time.sleep(random.uniform(3, 6))
    return []


def fetch_board_posts(code: str, name: str, market: str = "KRX") -> str:
    """
    종목 오픈톡 최신 메시지 → 프롬프트용 문자열 반환.
    캐시 TTL 내 재호출 시 캐시 반환.
    """
    cached = _cache.get(code)
    if cached and time.time() - cached["fetched_at"] < CACHE_TTL:
        return cached["text"]

    if market == "US":
        reuters = _US_REUTERS.get(code, code)
        url = f"https://m.stock.naver.com/worldstock/stock/{reuters}/discuss"
    else:
        url = f"https://m.stock.naver.com/domestic/stock/{code}/discuss"

    messages = _fetch_discuss(url)

    if not messages:
        _cache[code] = {"text": "", "fetched_at": time.time()}
        return ""

    lines = [f"=== {name} 오픈톡 최신 여론 ({len(messages)}개) ==="]
    for msg in messages[:20]:
        content = msg.get("content", "").strip()
        nick = msg.get("nickname", "익명")
        if content and len(content) > 3:
            lines.append(f"• {nick}: {content[:100]}")

    text = "\n".join(lines)
    _cache[code] = {"text": text, "fetched_at": time.time()}
    logger.info(f"[{name}] 오픈톡 {len(messages)}개 수집")
    return text
