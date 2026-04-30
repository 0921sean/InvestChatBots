"""
데이터 소스:
- 가격: yfinance (글로벌) + pykrx (한국)
- TA 지표: TradingView (tradingview-ta) — RSI/MACD/EMA
- 뉴스: RSS 피드
"""
import math
import logging
import time
from datetime import datetime, timedelta

import yfinance as yf
import feedparser
from tradingview_ta import TA_Handler, Interval

logger = logging.getLogger("investchat")

# ── 글로벌 종목 (yfinance) ────────────────────────────────
GLOBAL_TICKERS = {
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
    "NVDA":   "NVDA",
    "TSMC":   "TSM",
    "LRCX":   "LRCX",
}

# ── 한국 종목 (pykrx) — 코드: 이름 ───────────────────────
KRX_TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "015890": "이수페타시스",
    "006400": "삼성SDI",
    "000270": "기아",
    "005380": "현대차",
    "012450": "한화에어로스페이스",
    "089010": "켐트로닉스",
    "086520": "에코프로",
    "066970": "LG이노텍",
}
KRX_CODE_TO_NAME = KRX_TICKERS
KRX_NAME_TO_CODE = {v: k for k, v in KRX_TICKERS.items()}

# ── TradingView TA (가격 아님, RSI/MACD/EMA 전용) ─────────
TV_TA_SYMBOLS = {
    "NVDA":  ("NVDA",    "america", "NASDAQ"),
    "TSMC":  ("TSM",     "america", "NYSE"),
    "KOSPI": ("KOSPI",   "korea",   "KRX"),
    "BTC":   ("BTCUSDT", "crypto",  "BINANCE"),
    "삼성전자": ("005930", "korea",  "KRX"),
    "SK하이닉스": ("000660", "korea", "KRX"),
}

# ── 뉴스 피드 ─────────────────────────────────────────────
NEWS_FEEDS = [
    "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258&rss=true",
    "https://www.yonhapnewstv.co.kr/category/news/economy/feed/",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
]


def _yf_price(symbol: str):
    try:
        t = yf.Ticker(symbol)
        price = t.fast_info.last_price
        prev  = t.fast_info.previous_close
        if price is None or prev is None or math.isnan(price) or math.isnan(prev) or prev == 0:
            return None, None
        return round(price, 2), round((price - prev) / prev * 100, 2)
    except Exception as e:
        logger.debug(f"yfinance {symbol}: {e}")
        return None, None


def _pykrx_prices() -> dict:
    try:
        from pykrx import stock as krx
        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        result = {}
        for code, name in KRX_TICKERS.items():
            try:
                df = krx.get_market_ohlcv(start, today, code)
                if df is None or df.empty:
                    result[name] = None
                    continue
                price = float(df["종가"].iloc[-1])
                prev  = float(df["종가"].iloc[-2]) if len(df) > 1 else price
                chg   = (price - prev) / prev * 100 if prev else 0
                result[name] = {"symbol": code, "price": price, "change_pct": round(chg, 2)}
            except Exception:
                result[name] = None

        # KOSPI 지수
        try:
            df = krx.get_index_ohlcv(start, today, "1001")
            if df is not None and not df.empty:
                price = float(df["종가"].iloc[-1])
                prev  = float(df["종가"].iloc[-2]) if len(df) > 1 else price
                result["KOSPI"] = {"symbol": "1001", "price": price,
                                   "change_pct": round((price-prev)/prev*100, 2) if prev else 0}
        except Exception:
            pass
        return result
    except ImportError:
        return {}


def fetch_market_data() -> dict:
    result = {}

    # 글로벌 (yfinance)
    for name, symbol in GLOBAL_TICKERS.items():
        price, chg = _yf_price(symbol)
        result[name] = {"symbol": symbol, "price": price, "change_pct": chg}

    # 한국 (pykrx)
    kr = _pykrx_prices()
    for name, data in kr.items():
        if data:
            result[name] = data
        elif name not in result:
            result[name] = {"symbol": name, "price": None, "change_pct": None}

    return result


def fetch_stock_price(identifier: str) -> float | None:
    """단일 종목 현재가 (포트폴리오용)."""
    code = KRX_NAME_TO_CODE.get(identifier)
    if code:
        from pykrx import stock as krx
        try:
            today = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
            df = krx.get_market_ohlcv(start, today, code)
            return float(df["종가"].iloc[-1]) if df is not None and not df.empty else None
        except Exception:
            return None
    if identifier.isdigit() and len(identifier) == 6:
        return fetch_stock_price(KRX_CODE_TO_NAME.get(identifier, identifier))
    price, _ = _yf_price(identifier)
    return price


def fetch_technical_analysis() -> dict:
    """TradingView RSI/MACD/EMA — rate limit 방지를 위해 딜레이 포함."""
    result = {}
    for name, (symbol, screener, exchange) in TV_TA_SYMBOLS.items():
        try:
            h = TA_Handler(symbol=symbol, screener=screener,
                           exchange=exchange, interval=Interval.INTERVAL_1_DAY)
            a = h.get_analysis()
            result[name] = {
                "recommendation": a.summary.get("RECOMMENDATION", ""),
                "buy": a.summary.get("BUY", 0),
                "sell": a.summary.get("SELL", 0),
                "rsi":  round(a.indicators.get("RSI", 0), 1),
                "macd": round(a.indicators.get("MACD.macd", 0), 2),
                "ema20": round(a.indicators.get("EMA20", 0), 1),
                "ema50": round(a.indicators.get("EMA50", 0), 1),
            }
            time.sleep(0.3)  # rate limit 방지
        except Exception:
            result[name] = None
    return result


def fetch_news(max_items=12) -> list:
    seen = set()
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
                if title in seen:
                    continue
                seen.add(title)
                items.append({"title": title, "summary": getattr(entry, "summary", "")})
        except Exception:
            continue
    return items


def build_market_summary(market_data: dict, news: list, ta_data: dict = None) -> str:
    lines = ["=== 오늘의 시황 ==="]

    # 글로벌
    for name in ["NASDAQ", "S&P500", "NVDA", "TSMC", "LRCX"]:
        d = market_data.get(name, {})
        if d and d.get("price"):
            arrow = "▲" if (d.get("change_pct") or 0) >= 0 else "▼"
            ta = ta_data.get(name, {}) if ta_data else {}
            rsi_str = f" | RSI {ta['rsi']}" if ta and ta.get("rsi") else ""
            rec_str = f" | {ta['recommendation']}" if ta and ta.get("recommendation") else ""
            lines.append(f"{name}: {d['price']:,.2f} {arrow}{abs(d.get('change_pct') or 0):.1f}%{rsi_str}{rec_str}")

    # 한국 주요 종목
    kr_main = ["KOSPI", "삼성전자", "SK하이닉스", "삼성SDI", "현대차",
               "기아", "한화에어로스페이스", "이수페타시스", "켐트로닉스"]
    kr_data = [(n, market_data[n]) for n in kr_main
               if n in market_data and market_data[n] and market_data[n].get("price")]
    if kr_data:
        lines.append("\n=== 한국 주요 종목 ===")
        for name, d in kr_data:
            arrow = "▲" if (d.get("change_pct") or 0) >= 0 else "▼"
            ta = ta_data.get(name, {}) if ta_data else {}
            rsi_str = f" RSI {ta['rsi']}" if ta and ta.get("rsi") else ""
            unit = "원" if d.get("symbol", "").isdigit() else ""
            fmt = f"{d['price']:,.0f}{unit}" if unit else f"{d['price']:,.2f}"
            lines.append(f"{name}: {fmt} {arrow}{abs(d.get('change_pct') or 0):.1f}%{rsi_str}")

    lines.append("\n=== 주요 뉴스 ===")
    for item in news[:6]:
        lines.append(f"• {item['title']}")

    return "\n".join(lines)
