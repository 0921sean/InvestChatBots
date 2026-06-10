"""
네이버 금융 시세 상위 페이지에서 당일 급등·거래량 상위 종목을 발굴.
- 로그인·LLM 불필요 (토큰 0). pykrx가 KRX 로그인 벽에 막힌 것을 대체.
- 서브 사이클(워치리스트) 후보 소싱용 — "발굴·위험 종목"이 여기서 나온다.
반환: [{"name","code","market":"KR","source"}]
"""
import re
import logging
import urllib.request

logger = logging.getLogger("investchat.discovery")

_BASE = "https://finance.naver.com/sise/"

# 종목명 + 6자리 코드 (시세 상위 표의 종목 링크)
_ROW_RE = re.compile(r'/item/main\.naver\?code=(\d{6})"[^>]*class="tltle">([^<]+)</a>')

# ETF/ETN/리츠/스팩/인버스/레버리지 등 — 개별 발굴 대상이 아닌 상품 제외
_EXCLUDE_RE = re.compile(
    r"(KODEX|TIGER|KBSTAR|ARIRANG|HANARO|KOSEF|KINDEX|SOL\b|ACE\b|PLUS\b|TIMEFOLIO|"
    r"WON\b|RISE\b|인버스|레버리지|선물|ETN|리츠|스팩|커버드|채권|국고|2X|배당커버)"
)


def _fetch(path: str) -> str:
    req = urllib.request.Request(_BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=10).read().decode("euc-kr", "ignore")


def _parse(html: str) -> list[dict]:
    return [{"code": m.group(1), "name": m.group(2).strip()}
            for m in _ROW_RE.finditer(html)]


def fetch_kr_movers(top_rise: int = 12, top_quant: int = 8) -> list[dict]:
    """국장 급등률 상위 + 거래량 상위(코스피·코스닥)에서 후보 추출.
    각 페이지는 해당 지표 내림차순 정렬이라 상위 N개가 곧 당일 상위 종목.
    ETF·ETN·리츠 등은 제외. 중복(코드) 제거."""
    out: list[dict] = []
    seen: set[str] = set()
    sources = [
        ("sise_rise.naver?sosok=0", "급등", top_rise),    # 코스피 급등률
        ("sise_rise.naver?sosok=1", "급등", top_rise),    # 코스닥 급등률
        ("sise_quant.naver?sosok=0", "거래량", top_quant),  # 코스피 거래량
        ("sise_quant.naver?sosok=1", "거래량", top_quant),  # 코스닥 거래량
    ]
    for path, label, topn in sources:
        try:
            rows = _parse(_fetch(path))
        except Exception as e:
            logger.warning(f"네이버 발굴 {path} 실패: {e}")
            continue
        added = 0
        for r in rows:
            if added >= topn:
                break
            if _EXCLUDE_RE.search(r["name"]):
                continue
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            out.append({"name": r["name"], "code": r["code"],
                        "market": "KR", "source": f"네이버{label}"})
            added += 1
    logger.info(f"네이버 발굴: {len(out)}개 후보")
    return out


if __name__ == "__main__":
    for s in fetch_kr_movers():
        print(s)
