"""
미국 발굴 종목 수집 — Yahoo Finance 사전정의 스크리너(yfinance 내장).
- yfinance가 crumb/cookie를 자동 처리하므로 로그인·API키·스크래핑 불필요.
- Finviz는 무료로 표 데이터를 안 주고(JS 렌더+Elite auth) 막혀서 야후로 대체.
- 서브 사이클 미장(00시) 슬롯 후보 소싱용.
반환: [{"name","code","market":"US","source","change_pct"}]
"""
import logging

logger = logging.getLogger("investchat.discovery")


def _screen(scr: str, count: int) -> list[dict]:
    import yfinance as yf
    r = yf.screen(scr, count=count) or {}
    return r.get("quotes", []) or []


def fetch_us_movers(top_gainers: int = 12, top_active: int = 8) -> list[dict]:
    """급등(day_gainers) + 거래대금(most_actives) 상위에서 후보 추출.
    개별 주식(EQUITY)만, 워런트·우선주 등 특수 심볼 제외. 중복(심볼) 제거."""
    out: list[dict] = []
    seen: set[str] = set()
    sources = [("day_gainers", "급등", top_gainers), ("most_actives", "거래량", top_active)]
    for scr, label, topn in sources:
        try:
            quotes = _screen(scr, max(topn * 2, 25))
        except Exception as e:
            logger.warning(f"야후 스크리너 {scr} 실패: {e}")
            continue
        added = 0
        for q in quotes:
            if added >= topn:
                break
            if q.get("quoteType") != "EQUITY":   # ETF·펀드·인덱스 제외
                continue
            sym = (q.get("symbol") or "").strip().upper()
            if not sym or sym in seen:
                continue
            if any(c in sym for c in (".", "-", "^", "=")):  # 워런트/우선주/특수 심볼 제외
                continue
            seen.add(sym)
            name = (q.get("shortName") or q.get("displayName") or sym).strip()
            out.append({"name": name, "code": sym, "market": "US",
                        "source": f"야후{label}",
                        "change_pct": round(q.get("regularMarketChangePercent", 0) or 0, 1)})
            added += 1
    logger.info(f"야후 미국 발굴: {len(out)}개 후보")
    return out


if __name__ == "__main__":
    for s in fetch_us_movers():
        print(s)
