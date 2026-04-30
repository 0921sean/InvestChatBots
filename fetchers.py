import math
import logging
from datetime import datetime, timedelta

import yfinance as yf
import feedparser
from tradingview_ta import TA_Handler, Interval

logger = logging.getLogger("investchat")

# ── 글로벌 지수/종목 (yfinance) ───────────────────────────
GLOBAL_TICKERS = {
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
    "NVDA":   "NVDA",
    "TSMC":   "TSM",
    "LRCX":   "LRCX",
}

# ── 한국 종목 (pykrx — KRX 직접) ──────────────────────────
# 코드: 이름
KRX_TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "015890": "이수페타시스",
    "006400": "삼성SDI",
    "003490": "대한항공",
    "012450": "한화에어로스페이스",
    "004490": "세아베스틸지주",
    "000270": "기아",
    "005380": "현대차",
    "035420": "NAVER",
}

# ── 뉴스 피드 ─────────────────────────────────────────────
NEWS_FEEDS = [
    "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258&rss=true",
    "https://www.yonhapnewstv.co.kr/category/news/economy/feed/",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
]

# ── TradingView TA ────────────────────────────────────────
TV_SYMBOLS = {
    "NVDA":  ("NVDA",    "america", "NASDAQ"),
    "TSMC":  ("TSM",     "america", "NYSE"),
    "KOSPI": ("KOSPI",   "korea",   "KRX"),
    "BTC":   ("BTCUSDT", "crypto",  "BINANCE"),
}


def _fetch_yfinance(symbol: str):
    """yfinance로 가격 조회. 실패 시 None 반환."""
    try:
        t = yf.Ticker(symbol)
        price = t.fast_info.last_price
        prev = t.fast_info.previous_close
        if price is None or prev is None or math.isnan(price) or math.isnan(prev) or prev == 0:
            return None, None
        return round(price, 2), round((price - prev) / prev * 100, 2)
    except Exception as e:
        logger.debug(f"yfinance {symbol} 실패: {e}")
        return None, None


def _fetch_krx_prices():
    """pykrx로 한국 종목 일봉 조회."""
    try:
        from pykrx import stock as krx
        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        result = {}
        for code, name in KRX_TICKERS.items():
            try:
                df = krx.get_market_ohlcv(start, today, code)
                if df is None or df.empty:
                    result[name] = {"symbol": code, "price": None, "change_pct": None}
                    continue
                price = float(df["종가"].iloc[-1])
                prev = float(df["종가"].iloc[-2]) if len(df) > 1 else price
                chg = (price - prev) / prev * 100 if prev else 0
                result[name] = {"symbol": code, "price": price, "change_pct": round(chg, 2)}
            except Exception as e:
                logger.debug(f"pykrx {name} 실패: {e}")
                result[name] = {"symbol": code, "price": None, "change_pct": None}
        return result
    except ImportError:
        return {}


def fetch_market_data():
    """글로벌(yfinance) + 한국(pykrx) 통합 가격 조회."""
    result = {}

    # 글로벌
    for name, symbol in GLOBAL_TICKERS.items():
        price, chg = _fetch_yfinance(symbol)
        result[name] = {"symbol": symbol, "price": price, "change_pct": chg}

    # 한국 (pykrx)
    krx = _fetch_krx_prices()
    result.update(krx)

    # KOSPI 지수 (yfinance 실패 시 pykrx fallback)
    if "KOSPI" not in result or result.get("KOSPI", {}).get("price") is None:
        try:
            from pykrx import stock as krx_mod
            today = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
            df = krx_mod.get_index_ohlcv(start, today, "1001")  # KOSPI 지수
            if df is not None and not df.empty:
                price = float(df["종가"].iloc[-1])
                prev = float(df["종가"].iloc[-2]) if len(df) > 1 else price
                chg = (price - prev) / prev * 100 if prev else 0
                result["KOSPI"] = {"symbol": "1001", "price": price, "change_pct": round(chg, 2)}
        except Exception:
            if "KOSPI" not in result:
                result["KOSPI"] = {"symbol": "^KS11", "price": None, "change_pct": None}

    return result


def fetch_stock_price(symbol: str) -> float | None:
    """단일 종목 현재가 조회 (포트폴리오 추적용). 한국 종목 코드 또는 글로벌 티커."""
    # 한국 종목 코드 (6자리 숫자)
    if symbol.isdigit() and len(symbol) == 6:
        try:
            from pykrx import stock as krx
            today = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
            df = krx.get_market_ohlcv(start, today, symbol)
            if df is not None and not df.empty:
                return float(df["종가"].iloc[-1])
        except Exception:
            pass
        return None
    # 한국 종목 이름으로 코드 역조회
    name_to_code = {v: k for k, v in KRX_TICKERS.items()}
    if symbol in name_to_code:
        return fetch_stock_price(name_to_code[symbol])
    # 글로벌 티커
    price, _ = _fetch_yfinance(symbol)
    return price


def fetch_technical_analysis():
    """TradingView 기술적 지표 조회."""
    result = {}
    for name, (symbol, screener, exchange) in TV_SYMBOLS.items():
        try:
            h = TA_Handler(symbol=symbol, screener=screener,
                           exchange=exchange, interval=Interval.INTERVAL_1_DAY)
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


def fetch_news(max_items=12):
    """RSS 뉴스 피드 수집."""
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

    # 글로벌
    global_keys = list(GLOBAL_TICKERS.keys()) + ["KOSPI"]
    for name in global_keys:
        d = market_data.get(name, {})
        if d and d.get("price") is not None:
            arrow = "▲" if d["change_pct"] >= 0 else "▼"
            lines.append(f"{name}: {d['price']:,.2f} {arrow}{abs(d['change_pct']):.1f}%")

    # 한국 주요 종목
    kr_names = list(KRX_TICKERS.values())
    kr_data = [(n, market_data[n]) for n in kr_names if n in market_data and market_data[n].get("price")]
    if kr_data:
        lines.append("\n=== 한국 주요 종목 ===")
        for name, d in kr_data[:8]:
            arrow = "▲" if d["change_pct"] >= 0 else "▼"
            lines.append(f"{name}: {d['price']:,.0f}원 {arrow}{abs(d['change_pct']):.1f}%")

    if ta_data:
        lines.append("\n=== 기술적 지표 (TradingView) ===")
        for name, ta in ta_data.items():
            if ta:
                rec = (ta["recommendation"]
                       .replace("STRONG_", "강한 ")
                       .replace("BUY", "매수")
                       .replace("SELL", "매도")
                       .replace("NEUTRAL", "중립"))
                lines.append(f"{name}: {rec} | RSI {ta['rsi']} | EMA20 {ta['ema20']}")

    lines.append("\n=== 주요 뉴스 ===")
    for item in news[:6]:
        lines.append(f"• {item['title']}")

    return "\n".join(lines)
