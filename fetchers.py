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
    "KOSPI":  "^KS11",
    "KOSDAQ": "^KQ11",
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
    """yfinance fast_info로 현재가·변화율 조회.
    last_price만 있으면 가격 반환 (previous_close 없어도 변화율만 0으로).
    휴장 시 일부 종목의 previous_close가 NaN이라 둘 다 요구하면 가격까지 통째 누락됨."""
    try:
        t = yf.Ticker(symbol)
        price = t.fast_info.last_price
        prev  = t.fast_info.previous_close
        # last_price가 없으면 진짜 데이터 없음
        if price is None or (isinstance(price, float) and math.isnan(price)):
            return None, None
        # previous_close 없거나 NaN/0이면 가격만 반환 (변화율 0)
        if prev is None or (isinstance(prev, float) and math.isnan(prev)) or prev == 0:
            return round(float(price), 2), 0.0
        return round(float(price), 2), round((price - prev) / prev * 100, 2)
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
    """TradingView TA + yfinance 펀더멘털. KRX/US 모두 지원.
    두 소스 모두 실패하면 _data_unavailable=True로 표시."""
    result = {"name": name, "code": code, "market": market}
    tv_ok = False
    yf_ok = False

    screener = "america" if market == "US" else "korea"
    tv_exchange = exchange if market == "US" else "KRX"

    # TradingView TA — 가격 + 기술적 지표 (한국 종목은 KRX/KOSDAQ 둘 다 시도)
    tv_exchanges = [tv_exchange] if market == "US" else ["KRX", "KOSDAQ"]
    a = None
    for ex in tv_exchanges:
        try:
            h = TA_Handler(symbol=code, screener=screener, exchange=ex,
                           interval=Interval.INTERVAL_1_DAY)
            a = h.get_analysis()
            if a:
                tv_ok = True
                break
        except Exception as e:
            logger.debug(f"TV-TA {code}@{ex}: {e}")
    if a:
        try:
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
            logger.debug(f"TV-TA parse {code}: {e}")

    # yfinance — 펀더멘털 + 가격 fallback (일시적 429는 백오프 재시도)
    try:
        t = yf.Ticker(yf_ticker)
        info = {}
        for _attempt in range(2):                     # 3→2 fail-fast: 리밋 시 재무 last-good 캐시가 폴백(속도 병목 완화)
            try:
                info = t.info or {}
                if info:
                    break
            except Exception as _e:
                if _attempt == 1:
                    raise
                time.sleep(0.8)
        # TV 가격 없으면 yfinance에서 보완
        if "price" not in result:
            try:
                fi = t.fast_info
                p = getattr(fi, 'last_price', None) or getattr(fi, 'previous_close', None)
                if p:
                    result["price"] = round(float(p), 0) if market != "US" else round(float(p), 2)
                    yf_ok = True
            except Exception:
                pass
        for key, attr in [
            ("per", "trailingPE"), ("per_fwd", "forwardPE"),
            ("eps", "trailingEps"), ("roe", "returnOnEquity"),
            ("peg", "trailingPegRatio"), ("eps_growth", "earningsGrowth"),  # 성장주(GARP)(실적왕) GARP용
            ("market_cap", "marketCap"), ("52w_high", "fiftyTwoWeekHigh"),
            ("52w_low", "fiftyTwoWeekLow"), ("revenue", "totalRevenue"),
            ("debt_ratio", "debtToEquity"),
            # AI펀드 심화 — 마진·성장·현금흐름(새 데스크 프롬프트용, 현 committee 미사용)
            ("gross_margin", "grossMargins"), ("op_margin", "operatingMargins"),
            ("profit_margin", "profitMargins"), ("rev_growth", "revenueGrowth"),
            ("fcf", "freeCashflow"),
            ("business_summary", "longBusinessSummary"),  # '꿈꾸는 것'
        ]:
            val = info.get(attr)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                # 사업요약은 길어서 프롬프트 토큰 관리 위해 컷
                result[key] = val[:600] if key == "business_summary" and isinstance(val, str) else val
                yf_ok = True
    except Exception as e:
        logger.debug(f"yfinance {yf_ticker}: {e}")

    # 두 소스 모두 실패 or 데이터 너무 적음 → 플래그
    if not tv_ok and not yf_ok:
        result["_data_unavailable"] = True
    return result


def format_stock_data(data: dict) -> str:
    """봇 프롬프트용 종목 데이터 포맷.
    데이터 두 소스 다 실패 시 명시적으로 안내."""
    market = data.get("market", "KRX")
    currency = "$" if market == "US" else "₩"
    unit = "" if market == "US" else "원"
    lines = [f"=== {data['name']} ({data['code']}) — TradingView 기준 ==="]

    # 데이터 fetch 완전 실패 케이스
    if data.get("_data_unavailable"):
        lines.append("⚠️ 실시간 데이터 가져오기 실패 (TradingView·yfinance 둘 다 미응답).")
        lines.append("최근 시황 흐름과 텔레그램·블로그 시그널만으로 의견 주세요.")
        return "\n".join(lines)
    # 가용 지표가 1~2개뿐인 케이스
    fields = [k for k in ("price", "rsi", "ema20", "per", "roe", "eps", "52w_high") if data.get(k) is not None]
    if len(fields) <= 2:
        lines.append("⚠️ 데이터 일부만 수집됨 — 누락 지표는 봇이 보유 데이터로만 판단.")

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
        eps_str = f"${eps:,.2f}" if market == "US" else f"{eps:,.0f}원"
        lines.append(f"EPS: {eps_str}")

    roe = data.get("roe")
    if roe and not math.isnan(roe):
        lines.append(f"ROE: {roe*100:.1f}%")

    # 성장주(GARP)(실적왕) GARP 지표 — 없으면 명시(실적왕은 "데이터 없어 판단 보류")
    peg = data.get("peg")
    epsg = data.get("eps_growth")
    if peg is not None and not (isinstance(peg, float) and math.isnan(peg)):
        lines.append(f"PEG: {peg:.2f} ({'매력' if peg < 1 else '부담' if peg > 2 else '보통'})")
    else:
        lines.append("PEG: 데이터 없음")
    if epsg is not None and not (isinstance(epsg, float) and math.isnan(epsg)):
        gp = epsg * 100
        gp = 0.0 if abs(gp) < 0.5 else gp   # -0% 방지
        lines.append(f"EPS성장률: {gp:+.0f}%")
    else:
        lines.append("EPS성장률: 데이터 없음")

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
                # TradingView → yfinance → 토스증권(최종 폴백). yfinance 429 시 토스가 받아줌.
                price = None
                for exch in ("NASDAQ", "NYSE", "NYSE ARCA", "AMEX"):
                    try:
                        a = TA_Handler(symbol=code or symbol, screener="america",
                                       exchange=exch, interval=Interval.INTERVAL_1_DAY).get_analysis()
                        close = a.indicators.get("close") or a.indicators.get("Close")
                        if close:
                            price = float(close)
                            break
                    except Exception:
                        pass
                if not price:
                    price, _ = _yf_price(code or symbol)
                if not price:
                    try:
                        import toss
                        if toss.available():
                            price = toss.get_price(code or symbol)
                    except Exception:
                        pass
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


def _try_fetch_us_stock(ticker: str) -> dict | None:
    """US 티커 데이터 시도. TradingView → yfinance 폴백. 실패 시 None."""
    data: dict = {"name": ticker, "code": ticker, "market": "US"}

    # 1) TradingView TA
    for exchange in ("NASDAQ", "NYSE", "NYSE ARCA", "AMEX"):
        try:
            h = TA_Handler(symbol=ticker, screener="america",
                           exchange=exchange, interval=Interval.INTERVAL_1_DAY)
            a = h.get_analysis()
            close = a.indicators.get("close") or a.indicators.get("Close")
            if not close:
                continue
            data.update({
                "price": round(float(close), 2),
                "rsi": round(float(a.indicators.get("RSI") or 0), 1),
                "recommendation": a.summary.get("RECOMMENDATION", "NEUTRAL"),
                "buy_signals": a.summary.get("BUY", 0),
                "sell_signals": a.summary.get("SELL", 0),
            })
            break
        except Exception:
            continue

    # 2) yfinance — 가격 폴백 + 펀더멘털
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not data.get("price"):
            price, _ = _yf_price(ticker)
            if price:
                data["price"] = price
        for key, attr in [("per", "trailingPE"), ("eps", "trailingEps"),
                           ("52w_high", "fiftyTwoWeekHigh"), ("52w_low", "fiftyTwoWeekLow"),
                           ("market_cap", "marketCap"), ("roe", "returnOnEquity")]:
            val = info.get(attr)
            if val and not (isinstance(val, float) and math.isnan(val)):
                data[key] = val
        # 회사 이름이 있으면 표시명으로 사용
        name = info.get("shortName") or info.get("longName")
        if name:
            data["name"] = name
    except Exception:
        pass

    return data if data.get("price") else None


_ticker_search_cache: dict = {}   # {name_lower: ticker or None}


def _search_ticker(name: str) -> str | None:
    """회사명 → 티커 (Yahoo 라이브 검색). 최상단 EQUITY·미국 보통주 티커만.
    하드코딩 표에 없는 최신 상장(예: SpaceX→SPCX)을 실시간으로 해석. 실패 시 None. 결과 캐시."""
    import re
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in _ticker_search_cache:
        return _ticker_search_cache[key]
    result = None
    try:
        s = yf.Search(name, max_results=6)
        for q in (getattr(s, "quotes", None) or []):
            if q.get("quoteType") != "EQUITY":
                continue
            sym = (q.get("symbol") or "").strip().upper()
            if sym and re.fullmatch(r"[A-Z]{1,5}", sym):  # 미국 보통주 형태만(.NE 등·지수 제외)
                result = sym
                break
    except Exception:
        result = None
    _ticker_search_cache[key] = result
    return result


def detect_and_fetch_stocks(message: str) -> str:
    """
    메시지에서 종목 감지 → TradingView 데이터 페치 → 포맷 문자열 반환.
    한글 종목명, 영문 회사명, US 티커 모두 인식.
    """
    import re

    # ── 한글/영문 회사명 → 티커 매핑 ──────────────────────
    KR_NAME_MAP = {
        # 미국 상장 (한글)
        "온다스": ("ONDS", "US"), "엔비디아": ("NVDA", "US"), "애플": ("AAPL", "US"),
        "마이크로소프트": ("MSFT", "US"), "테슬라": ("TSLA", "US"), "아마존": ("AMZN", "US"),
        "구글": ("GOOGL", "US"), "알파벳": ("GOOGL", "US"), "메타": ("META", "US"),
        "넷플릭스": ("NFLX", "US"), "AMD": ("AMD", "US"), "인텔": ("INTC", "US"),
        "퀄컴": ("QCOM", "US"), "브로드컴": ("AVGO", "US"), "팔란티어": ("PLTR", "US"),
        "세일즈포스": ("CRM", "US"), "오라클": ("ORCL", "US"), "IBM": ("IBM", "US"),
        "비자": ("V", "US"), "마스터카드": ("MA", "US"), "버크셔": ("BRK-B", "US"),
        "존슨앤존슨": ("JNJ", "US"), "화이자": ("PFE", "US"), "모더나": ("MRNA", "US"),
        "코인베이스": ("COIN", "US"), "스포티파이": ("SPOT", "US"), "우버": ("UBER", "US"),
        "에어비앤비": ("ABNB", "US"), "세레브라스": ("CRWV", "US"),
        "TSMC": ("TSM", "US"),
        "샌디스크": ("SNDK", "US"), "리비안": ("RIVN", "US"), "루시드": ("LCID", "US"),
        "웨스턴디지털": ("WDC", "US"), "마이크론": ("MU", "US"), "코스트코": ("COST", "US"),
        "스타벅스": ("SBUX", "US"), "디즈니": ("DIS", "US"), "보잉": ("BA", "US"),
        # 한국 상장 (한글)
        "삼성전자": ("005930", "KR"), "하이닉스": ("000660", "KR"), "SK하이닉스": ("000660", "KR"),
        "삼성바이오": ("207940", "KR"), "삼성바이오로직스": ("207940", "KR"),
        "셀트리온": ("068270", "KR"), "카카오": ("035720", "KR"), "네이버": ("035420", "KR"),
        "현대차": ("005380", "KR"), "기아": ("000270", "KR"), "포스코": ("005490", "KR"),
        "LG에너지솔루션": ("373220", "KR"), "에코프로비엠": ("247540", "KR"),
        "에코프로": ("086520", "KR"), "한화에어로스페이스": ("012450", "KR"),
        "LIG넥스원": ("079550", "KR"), "현대로템": ("064350", "KR"),
        "HD한국조선해양": ("009540", "KR"), "한화오션": ("042660", "KR"),
        "삼성SDI": ("006400", "KR"), "LG화학": ("051910", "KR"),
    }
    EN_NAME_MAP = {
        "NOKIA": "NOK", "APPLE": "AAPL", "TESLA": "TSLA", "GOOGLE": "GOOGL",
        "AMAZON": "AMZN", "MICROSOFT": "MSFT", "NVIDIA": "NVDA",
    }
    SKIP = {"NTM", "EPS", "PER", "ROE", "RSI", "ETF", "IPO", "GDP", "FED",
            "AI", "US", "IT", "OK", "TV", "ID", "KRX", "FOR", "AND", "THE",
            "NOT", "ARE", "NMF", "TTM", "YOY", "QOQ", "FCF", "DCF", "NAV",
            "THIS", "THAT", "WITH", "FROM", "HAVE", "BEEN", "WILL", "WHAT",
            "WHEN", "WHERE"}

    candidates_us, candidates_kr = [], []

    # 1) 한글 이름 매핑
    for name, (ticker, market) in KR_NAME_MAP.items():
        if name in message:
            if market == "US":
                candidates_us.append(ticker)
            else:
                candidates_kr.append((name, ticker))   # 한글명 보존(헤더 라벨용)

    # 2) 영문 대문자 티커/이름 감지
    pat = r'(?<![A-Za-z])([A-Z]{2,5}|[A-Z][a-z]{2,9})(?![A-Za-z])'
    for raw in re.findall(pat, message):
        upper = raw.upper()
        if upper in EN_NAME_MAP:
            candidates_us.append(EN_NAME_MAP[upper])
        elif upper not in SKIP and upper not in candidates_us:
            candidates_us.append(upper)

    # 중복 제거
    candidates_us = list(dict.fromkeys(candidates_us))
    candidates_kr = list(dict.fromkeys(candidates_kr))

    results = []

    # KR 종목 조회
    for kr_name, code in candidates_kr[:2]:
        try:
            data = fetch_stock_data(code, f"{code}.KS", kr_name, market="KRX", exchange="KRX")
            if data:
                results.append(format_stock_data(data))
                time.sleep(0.3)
        except Exception:
            pass

    # US 종목 조회
    for ticker in candidates_us[:3]:
        data = _try_fetch_us_stock(ticker)
        if data:
            results.append(format_stock_data(data))
            time.sleep(0.3)

    # 폴백: 하드코딩 표·티커로 아무것도 못 찾았을 때만 → 회사명을 Yahoo 라이브 검색으로 해석
    # (예: "SpaceX" → SPCX). 오탐·지연 최소화 위해 '아무 결과 없을 때'로 한정.
    if not results:
        # 회사명처럼 보이는 토큰(첫글자 대문자 또는 내부 대문자: SpaceX·Rivian)만, 최대 2개
        name_toks = [t for t in re.findall(r'[A-Za-z][a-zA-Z]{2,15}', message)
                     if t.upper() not in SKIP and (t[0].isupper() or any(c.isupper() for c in t[1:]))]
        for tok in name_toks[:2]:
            alt = _search_ticker(tok)
            if alt:
                data = _try_fetch_us_stock(alt)
                if data:
                    results.append(format_stock_data(data))
                    break

    return "\n\n".join(results)


def _record_price_health(ok: bool):
    """시세 fetch 성공/실패 기록(P0 헬스체크) — 오늘 실패율 임계(10건+·50%+) 시 오너 ntfy(쿨다운 내장)."""
    try:
        from db import record_fetch
        okc, fail = record_fetch("price", ok)
        if not ok and fail >= 10 and fail / max(okc + fail, 1) > 0.5:
            from notifier import notify
            notify("⚠️ 시세 데이터 실패율 경고",
                   f"오늘 price fetch {okc}성공/{fail}실패 — yfinance 리밋/장애 가능. 사이클 판단 품질 저하 주의.")
    except Exception:
        pass


def fetch_stock_price(symbol: str) -> float | None:
    """단일 종목 현재가. 소스 우선순위: TradingView → yfinance → 토스증권(최종 폴백).
    yfinance 429 장애 시 토스가 받아준다(자격증명 없으면 자동 스킵)."""
    try:
        h = TA_Handler(symbol=symbol, screener="korea", exchange="KRX",
                       interval=Interval.INTERVAL_1_DAY)
        a = h.get_analysis()
        close = a.indicators.get("close") or a.indicators.get("Close")
        if close:
            _record_price_health(True)
            return float(close)
    except Exception:
        pass
    price, _ = _yf_price(symbol)
    if not price and "." not in symbol:        # 최종 폴백: 토스증권(미장 티커만)
        try:
            import toss
            if toss.available():
                price = toss.get_price(symbol)
        except Exception:
            pass
    _record_price_health(price is not None)
    return price
