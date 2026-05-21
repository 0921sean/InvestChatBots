"""
텔레그램 대체 종목 발굴 소스
- 네이버 금융 뉴스 RSS
- DART 전자공시 (실적 발표)
- Google 뉴스 (한국 증시)
- Yahoo Finance 어닝 캘린더
언급된 종목은 watched_stocks DB에 영구 저장
"""
import re
import json
import time
import random
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("investchat.news")

KST = timezone(timedelta(hours=9))

def _get_main_sector_names() -> set:
    """순환 import 없이 메인 섹터 종목명 반환."""
    try:
        import importlib
        orch = importlib.import_module("orchestrator")
        return {s["name"] for sec in orch.SECTORS for s in sec["stocks"]}
    except Exception:
        # fallback: 하드코딩
        return {
            "삼성전자","SK하이닉스","한미반도체",
            "LG에너지솔루션","삼성SDI","에코프로비엠",
            "삼성바이오로직스","셀트리온","유한양행",
            "한화에어로스페이스","LIG넥스원","현대로템",
            "현대차","기아","현대모비스",
            "HD한국조선해양","한화오션","HMM",
            "NAVER","카카오","크래프톤",
            "KB금융","신한지주","삼성화재",
            "POSCO홀딩스","현대제철","LG화학",
            "SK이노베이션","한국가스공사","한화솔루션",
            "SK텔레콤","KT","LG유플러스",
            "엔비디아","마이크로소프트","알파벳","메타","애플",
            "TSMC","브로드컴","팔란티어","AMD",
        }


_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]

# 알려진 한국 종목 키워드 → 코드 매핑
KR_STOCK_KEYWORDS = {
    "삼성전자": "005930", "SK하이닉스": "000660", "한미반도체": "042700",
    "LG에너지솔루션": "373220", "삼성SDI": "006400", "에코프로비엠": "247540",
    "삼성바이오로직스": "207940", "셀트리온": "068270", "유한양행": "000100",
    "한화에어로스페이스": "012450", "LIG넥스원": "079550", "현대로템": "064350",
    "현대차": "005380", "기아": "000270", "현대모비스": "012330",
    "HD한국조선해양": "009540", "한화오션": "042660", "HMM": "011200",
    "삼성중공업": "010140", "NAVER": "035420", "카카오": "035720",
    "크래프톤": "259960", "KB금융": "105560", "신한지주": "055550",
    "삼성화재": "000810", "POSCO홀딩스": "005490", "현대제철": "004020",
    "LG화학": "051910", "SK이노베이션": "096770", "한국가스공사": "036460",
    "한화솔루션": "009830", "SK텔레콤": "017670", "KT": "030200",
    "LG유플러스": "032640", "에코프로": "086520", "포스코홀딩스": "005490",
}


def _fetch(url: str) -> str:
    try:
        headers = {"User-Agent": random.choice(_USER_AGENTS),
                   "Accept-Language": "ko-KR,ko;q=0.9"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug(f"fetch 오류 {url}: {e}")
        return ""


_yf_code_cache: dict = {}  # {code: {"name": str, "valid": bool}}

def _lookup_kr_code(code: str) -> dict | None:
    """6자리 코드 → yfinance로 종목명 확인. 캐시 사용."""
    if code in _yf_code_cache:
        r = _yf_code_cache[code]
        return r if r["valid"] else None
    try:
        import yfinance as yf
        t = yf.Ticker(f"{code}.KS")
        price = t.fast_info.last_price or t.fast_info.previous_close
        if not price:
            _yf_code_cache[code] = {"valid": False}
            return None
        info = t.info
        long_name = info.get("longName", "") or ""
        short_name = info.get("shortName", "") or ""
        # 짧고 한글 포함된 이름 우선, 없으면 영문 이름
        name = short_name if short_name and len(short_name) < 20 else long_name
        if not name or "," in name or len(name) > 30:
            name = code  # fallback
        name = (name.replace(" Co., Ltd.", "").replace(" Co.,Ltd.", "")
                .replace(" Corp.", "").replace(" Inc.", "").strip())
        result = {"name": name, "code": code, "market": "KR", "valid": True}
        _yf_code_cache[code] = result
        return result
    except Exception:
        _yf_code_cache[code] = {"valid": False}
        return None


def _extract_kr_stocks(text: str) -> list[dict]:
    """텍스트에서 한국 종목 추출 — 키워드 매핑 + 6자리 코드 직접 파싱."""
    found = []
    seen_codes = set()

    # 1. 알려진 종목명 매핑
    for name, code in KR_STOCK_KEYWORDS.items():
        if name in text and code not in seen_codes:
            found.append({"name": name, "code": code, "market": "KR"})
            seen_codes.add(code)

    # 2. 6자리 숫자 코드 직접 파싱 (예: A005930, (005930), 종목코드 005930)
    codes_in_text = re.findall(r'\b([0-9]{6})\b', text)
    for code in codes_in_text:
        if code not in seen_codes:
            result = _lookup_kr_code(code)
            if result:
                found.append(result)
                seen_codes.add(code)

    return found


def fetch_naver_stock_news() -> tuple[str, list[dict]]:
    """네이버 금융 뉴스에서 종목 코드 직접 추출 (stockCode 파라미터)."""
    urls = [
        "https://finance.naver.com/news/mainnews.naver",
        "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258",
    ]
    all_titles = []
    all_stocks = []
    seen_codes = set()

    for url in urls:
        raw = _fetch(url)
        if not raw:
            continue
        # code=XXXXXX 또는 stockCode=XXXXXX 패턴 추출
        codes = re.findall(r'(?:stock)?[Cc]ode=([0-9]{6})', raw)
        # 기사 제목 추출
        titles = re.findall(r'<a[^>]+class="[^"]*title[^"]*"[^>]*>([^<]+)</a>', raw)
        if not titles:
            titles = re.findall(r'<strong[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</strong>', raw)

        for code in codes:
            if code not in seen_codes:
                result = _lookup_kr_code(code)
                if result:
                    all_stocks.append(result)
                    seen_codes.add(code)

        for t in titles[:10]:
            t = t.strip()
            if t and len(t) > 5:
                all_titles.append(t)
                # 제목에서도 6자리 코드 파싱
                for c in re.findall(r'\b([0-9]{6})\b', t):
                    if c not in seen_codes:
                        r = _lookup_kr_code(c)
                        if r:
                            all_stocks.append(r)
                            seen_codes.add(c)

        time.sleep(0.5)

    text = ""
    if all_titles or all_stocks:
        stock_names = [s["name"] for s in all_stocks]
        text = f"=== 네이버 증권 뉴스 (종목 {len(all_stocks)}개 감지) ===\n"
        if stock_names:
            text += f"언급 종목: {', '.join(stock_names[:10])}\n"
        text += "\n".join(f"• {t}" for t in all_titles[:8])

    return text, all_stocks


def fetch_naver_finance_news() -> tuple[str, list[dict]]:
    """네이버 증권 뉴스 + Google 뉴스 통합."""
    all_texts = []
    all_stocks = []

    # 네이버 증권 뉴스 (stockCode 파싱)
    t, s = fetch_naver_stock_news()
    if t:
        all_texts.append(t)
    all_stocks.extend(s)

    time.sleep(0.5)

    # Google 뉴스 보조
    for q in ["코스닥 급등 종목", "실적 서프라이즈 주가"]:
        t, s = fetch_google_finance_news(q)
        if t:
            all_texts.append(t)
        all_stocks.extend(s)
        time.sleep(0.3)

    return "\n\n".join(all_texts), all_stocks


def fetch_dart_disclosures() -> tuple[str, list[dict]]:
    """DART 전자공시 — Google 뉴스로 실적 공시 키워드 검색."""
    try:
        text, stocks = fetch_google_finance_news("DART 전자공시 실적 영업이익")
        if text:
            text = text.replace("뉴스 'DART 전자공시 실적 영업이익'", "DART/공시")
        return text, stocks
    except Exception as e:
        logger.debug(f"DART 오류: {e}")
        return "", []


def fetch_google_finance_news(query: str = "한국 주식 실적") -> tuple[str, list[dict]]:
    """Google 뉴스 RSS로 한국 증시 뉴스."""
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
        raw = _fetch(url)
        if not raw:
            return "", []

        titles = re.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', raw)
        if not titles:
            titles = re.findall(r'<title>(.+?)</title>', raw)
        titles = [t for t in titles[1:] if t and "Google" not in t][:12]

        stocks = []
        for t in titles:
            stocks.extend(_extract_kr_stocks(t))

        text = ""
        if titles:
            text = f"=== 뉴스 '{query}' ({len(titles)}개) ===\n" + "\n".join(f"• {t}" for t in titles)

        return text, stocks
    except Exception as e:
        logger.debug(f"Google 뉴스 오류: {e}")
        return "", []


def collect_all_news_stocks() -> str:
    """전체 뉴스 소스에서 종목 수집 → watched_stocks에 저장 → 컨텍스트 반환."""
    from db import upsert_watched_stock

    all_texts = []
    all_stocks = []

    # 네이버 금융
    text, stocks = fetch_naver_finance_news()
    if text:
        all_texts.append(text)
    all_stocks.extend(stocks)

    time.sleep(1)

    # DART 공시
    text, stocks = fetch_dart_disclosures()
    if text:
        all_texts.append(text)
    all_stocks.extend(stocks)

    time.sleep(1)

    # Google 뉴스 (여러 쿼리)
    for query in ["한국 주식 실적 발표", "코스피 주목 종목", "어닝서프라이즈"]:
        text, stocks = fetch_google_finance_news(query)
        if text:
            all_texts.append(text)
        all_stocks.extend(stocks)
        time.sleep(0.5)

    # 메인 섹터 종목은 저장 안 함 (워치리스트 중복 방지)
    main_names = _get_main_sector_names()

    # 중복 제거 후 DB 저장
    seen = set()
    for s in all_stocks:
        if s["name"] not in seen and s["name"] not in main_names:
            seen.add(s["name"])
            upsert_watched_stock(s["name"], s.get("code"), s.get("market", "KR"), "뉴스")
            logger.info(f"관심 종목 추가: {s['name']}")

    return "\n\n".join(all_texts)


def get_watched_stocks_for_watchlist() -> list[dict]:
    """워치리스트용 관심 종목 반환 (최근 7일 이내 언급된 것)."""
    from db import get_watched_stocks
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=7)).isoformat()

    stocks = get_watched_stocks(limit=100)
    return [s for s in stocks if s.get("last_seen", "") >= cutoff]
