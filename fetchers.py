"""
데이터 소스:
- 가격 + TA 지표: TradingView (tradingview-ta)
- 펀더멘털 (PER, EPS 등): yfinance
- 뉴스: RSS 피드
"""
import logging
import time
import math
from datetime import datetime, timedelta

import yfinance as yf
import feedparser
from tradingview_ta import TA_Handler, Interval

logger = logging.getLogger("investchat")

# ── 글로벌 시황용 종목 ────────────────────────────────────
GLOBAL_TICKERS = {
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
    "NVDA":   "NVDA",
    "TSMC":   "TSM",
}

KRX_TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "012450": "한화에어로스페이스",
    "373220": "LG에너지솔루션",
}

TV_TA_SYMBOLS = {
    "NVDA":    ("NVDA",    "america", "NASDAQ"),
    "TSMC":    ("TSM",     "america", "NYSE"),
    "삼성전자":  ("005930", "korea",   "KRX"),
    "SK하이닉스": ("000660", "korea",  "KRX"),
}

NEWS_FEEDS = [
    "https://www.yonhapnewstv.co.kr/category/news/economy/feed/",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
]


# ── 글로벌 시황 데이터 ────────────────────────────────────
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


def fetch_market_data() -> dict:
    result = {}
    for name, symbol in GLOBAL_TICKERS.items():
        price, chg = _yf_price(symbol)
        result[name] = {"symbol": symbol, "price": price, "change_pct": chg}

    # 한국 주요 종목 (pykrx)
    try:
        from pykrx import stock as krx
        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        for code, name in KRX_TICKERS.items():
            try:
                df = krx.get_market_ohlcv(start, today, code)
                if df is None or df.empty:
                    continue
                price = float(df["종가"].iloc[-1])
                prev  = float(df["종가"].iloc[-2]) if len(df) > 1 else price
                chg   = (price - prev) / prev * 100 if prev else 0
                result[name] = {"symbol": code, "price": price, "change_pct": round(chg, 2)}
            except Exception:
                pass
    except ImportError:
        pass
    return result


def fetch_technical_analysis() -> dict:
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
                "rsi":  round(a.indicators.get("RSI", 0) or 0, 1),
                "macd": round(a.indicators.get("MACD.macd", 0) or 0, 2),
                "ema20": round(a.indicators.get("EMA20", 0) or 0, 1),
            }
            time.sleep(0.3)
        except Exception:
            result[name] = None
    return result


def fetch_news(max_items=10) -> list:
    seen, items = set(), []
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
    lines = ["=== 시황 ==="]
    for name in ["NASDAQ", "S&P500", "NVDA", "TSMC"]:
        d = market_data.get(name, {})
        if d and d.get("price"):
            arrow = "▲" if (d.get("change_pct") or 0) >= 0 else "▼"
            ta = (ta_data or {}).get(name, {})
            rsi_str = f" RSI {ta['rsi']}" if ta and ta.get("rsi") else ""
            lines.append(f"{name}: {d['price']:,.2f} {arrow}{abs(d.get('change_pct') or 0):.1f}%{rsi_str}")

    kr_names = ["삼성전자", "SK하이닉스", "LG에너지솔루션", "한화에어로스페이스"]
    kr_items = [(n, market_data[n]) for n in kr_names if n in market_data and market_data[n] and market_data[n].get("price")]
    if kr_items:
        lines.append("=== 국내 주요 ===")
        for name, d in kr_items:
            arrow = "▲" if (d.get("change_pct") or 0) >= 0 else "▼"
            lines.append(f"{name}: {d['price']:,.0f}원 {arrow}{abs(d.get('change_pct') or 0):.1f}%")

    if news:
        lines.append("=== 주요 뉴스 ===")
        for item in news[:5]:
            lines.append(f"• {item['title']}")

    return "\n".join(lines)


# ── 개별 종목 데이터 (봇 분석용) ─────────────────────────
def fetch_stock_data(code: str, yf_ticker: str, name: str,
                     market: str = "KRX", exchange: str = "KRX") -> dict:
    """TradingView TA + yfinance 펀더멘털. KRX/US 모두 지원."""
    result = {"name": name, "code": code, "market": market}

    screener = "america" if market == "US" else "korea"
    tv_exchange = exchange if market == "US" else "KRX"

    # TradingView TA — 가격 + 기술적 지표
    try:
        h = TA_Handler(symbol=code, screener=screener, exchange=tv_exchange,
                       interval=Interval.INTERVAL_1_DAY)
        a = h.get_analysis()
        close = a.indicators.get("close") or a.indicators.get("Close")
        open_p = a.indicators.get("open") or a.indicators.get("Open")
        if close:
            result["price"] = round(float(close), 0)
        if close and open_p and open_p != 0:
            result["change_pct"] = round((float(close) - float(open_p)) / float(open_p) * 100, 2)
        rsi = a.indicators.get("RSI")
        if rsi:
            result["rsi"] = round(float(rsi), 1)
        result["recommendation"] = a.summary.get("RECOMMENDATION", "NEUTRAL")
        result["buy_signals"] = a.summary.get("BUY", 0)
        result["sell_signals"] = a.summary.get("SELL", 0)
        ema20 = a.indicators.get("EMA20")
        ema50 = a.indicators.get("EMA50")
        if ema20:
            result["ema20"] = round(float(ema20), 0)
        if ema50:
            result["ema50"] = round(float(ema50), 0)
    except Exception as e:
        logger.debug(f"TV-TA {code}: {e}")

    # yfinance — 펀더멘털
    try:
        t = yf.Ticker(yf_ticker)
        info = t.info
        for key, attr in [
            ("per", "trailingPE"), ("per_fwd", "forwardPE"),
            ("eps", "trailingEps"), ("roe", "returnOnEquity"),
            ("market_cap", "marketCap"), ("52w_high", "fiftyTwoWeekHigh"),
            ("52w_low", "fiftyTwoWeekLow"), ("revenue", "totalRevenue"),
            ("debt_ratio", "debtToEquity"),
        ]:
            val = info.get(attr)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                result[key] = val
    except Exception as e:
        logger.debug(f"yfinance {yf_ticker}: {e}")

    return result


def format_stock_data(data: dict) -> str:
    """봇 프롬프트용 종목 데이터 포맷."""
    market = data.get("market", "KRX")
    currency = "$" if market == "US" else "₩"
    unit = "" if market == "US" else "원"
    lines = [f"=== {data['name']} ({data['code']}) — TradingView 기준 ==="]

    price = data.get("price")
    if price:
        change = data.get("change_pct", 0) or 0
        arrow = "▲" if change >= 0 else "▼"
        fmt = f"{currency}{price:,.2f}" if market == "US" else f"{price:,.0f}{unit}"
        lines.append(f"현재가: {fmt} {arrow}{abs(change):.1f}%")

    rsi = data.get("rsi")
    if rsi:
        rsi_comment = "과매수" if rsi > 70 else "과매도" if rsi < 30 else "중립"
        lines.append(f"RSI(14): {rsi} ({rsi_comment})")

    rec = data.get("recommendation", "")
    if rec:
        rec_kr = {
            "STRONG_BUY": "강력매수", "BUY": "매수", "NEUTRAL": "중립",
            "SELL": "매도", "STRONG_SELL": "강력매도"
        }.get(rec, rec)
        buy = data.get("buy_signals", 0)
        sell = data.get("sell_signals", 0)
        lines.append(f"TradingView: {rec_kr} (매수신호 {buy} / 매도신호 {sell})")

    ema20 = data.get("ema20")
    if ema20 and price:
        pos = "위" if price > ema20 else "아래"
        lines.append(f"EMA20: {ema20:,.0f}원 (현재가 {pos})")

    per = data.get("per")
    if per and not math.isnan(per):
        lines.append(f"PER: {per:.1f}x")

    eps = data.get("eps")
    if eps and not math.isnan(eps):
        lines.append(f"EPS: {eps:,.0f}원")

    roe = data.get("roe")
    if roe and not math.isnan(roe):
        lines.append(f"ROE: {roe*100:.1f}%")

    high52 = data.get("52w_high")
    low52 = data.get("52w_low")
    if high52 and low52 and price:
        pct_from_high = (price - high52) / high52 * 100
        lines.append(f"52주: 최고 {high52:,.0f} / 최저 {low52:,.0f} (고점比 {pct_from_high:.1f}%)")

    return "\n".join(lines)


def fetch_stock_news(stock_name: str, yf_ticker: str, max_items: int = 10) -> list[dict]:
    """특정 종목 관련 최근 뉴스 수집 (yfinance + 네이버 RSS)."""
    items = []
    seen = set()

    # yfinance 뉴스
    try:
        t = yf.Ticker(yf_ticker)
        for article in (t.news or [])[:max_items]:
            title = article.get("title", "")
            if title and title not in seen:
                seen.add(title)
                items.append({
                    "title": title,
                    "summary": article.get("summary", ""),
                    "source": article.get("publisher", ""),
                })
    except Exception as e:
        logger.debug(f"yfinance news {yf_ticker}: {e}")

    # 네이버 금융 종목 뉴스 RSS
    naver_url = f"https://finance.naver.com/item/news_news.naver?code={yf_ticker.replace('.KS','')}&page=1&sm=title_entity_id.basic&clusterId="
    try:
        feed = feedparser.parse(
            f"https://finance.naver.com/item/news.naver?code={yf_ticker.replace('.KS','')}&rss=true"
        )
        for entry in feed.entries[:max_items]:
            title = getattr(entry, "title", "")
            if title and title not in seen:
                seen.add(title)
                items.append({
                    "title": title,
                    "summary": getattr(entry, "summary", ""),
                    "source": "네이버금융",
                })
    except Exception:
        pass

    return items[:max_items]


def format_stock_news(stock_name: str, news_items: list[dict]) -> str:
    if not news_items:
        return f"=== {stock_name} 최근 뉴스 없음 ==="
    lines = [f"=== {stock_name} 최근 뉴스/공시 ({len(news_items)}건) ==="]
    for item in news_items:
        src = f" [{item['source']}]" if item.get("source") else ""
        lines.append(f"• {item['title']}{src}")
        if item.get("summary"):
            lines.append(f"  {item['summary'][:100]}")
    return "\n".join(lines)


def fetch_position_prices(positions: list[dict]) -> dict[str, float]:
    """
    보유 포지션 현재가 일괄 조회.
    {symbol: current_price} 반환. 무료 (tradingview-ta + yfinance).
    """
    result = {}
    for pos in positions:
        code   = pos.get("code") or ""
        symbol = pos.get("symbol", "")
        market = pos.get("market", "KRX")
        key    = symbol

        try:
            if market == "US":
                price, _ = _yf_price(code or symbol)
                if price:
                    result[key] = price
            else:
                # KRX: tradingview-ta 우선, 실패 시 yfinance
                try:
                    h = TA_Handler(symbol=code, screener="korea", exchange="KRX",
                                   interval=Interval.INTERVAL_1_DAY)
                    a = h.get_analysis()
                    close = a.indicators.get("close") or a.indicators.get("Close")
                    if close:
                        result[key] = float(close)
                        continue
                except Exception:
                    pass
                yf_ticker = code + ".KS" if code else ""
                if yf_ticker:
                    price, _ = _yf_price(yf_ticker)
                    if price:
                        result[key] = price
        except Exception as e:
            logger.debug(f"현재가 조회 실패 {symbol}: {e}")
        time.sleep(0.2)

    return result


def fetch_stock_price(symbol: str) -> float | None:
    """단일 종목 현재가."""
    try:
        h = TA_Handler(symbol=symbol, screener="korea", exchange="KRX",
                       interval=Interval.INTERVAL_1_DAY)
        a = h.get_analysis()
        close = a.indicators.get("close") or a.indicators.get("Close")
        return float(close) if close else None
    except Exception:
        pass
    price, _ = _yf_price(symbol)
    return price
