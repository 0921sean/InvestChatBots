"""
트레이딩 규칙엔진용 브로드 유니버스 정적 리스트 생성기 (1회성/주기적 갱신).
결과를 universe_us.txt / universe_kr.txt 로 커밋 → 스캐너는 이 파일만 읽음(결정론적).

  · US = S&P500 (datasets CSV) — 대형 유동주 대부분 + 나스닥100 상당수 포함.
  · KR = KOSPI200 (네이버 지수구성 페이지) — 대형 유동주.
  · KOSDAQ150·나스닥100 단독분은 안정적 소스 확보 후 추가(v1 보류).

  python build_universe.py    # 두 파일 갱신
"""
import os
import re
import urllib.request

US_OUT = os.path.join(os.path.dirname(__file__), "universe_us.txt")
KR_OUT = os.path.join(os.path.dirname(__file__), "universe_kr.txt")


def _get(url: str, enc: str = "utf-8") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=25).read().decode(enc, "ignore")


def fetch_sp500() -> list[str]:
    csv = _get("https://raw.githubusercontent.com/datasets/"
               "s-and-p-500-companies/main/data/constituents.csv")
    syms = []
    for line in csv.splitlines()[1:]:
        if not line:
            continue
        sym = line.split(",")[0].strip().upper()
        if sym:
            syms.append(sym.replace(".", "-"))   # yfinance: BRK.B → BRK-B
    return sorted(set(syms))


def fetch_kospi200() -> list[str]:
    codes: set[str] = set()
    for page in range(1, 21):   # 10종목/페이지 × 20 = 200
        try:
            h = _get(f"https://finance.naver.com/sise/entryJongmok.naver"
                     f"?type=KPI200&page={page}", "euc-kr")
        except Exception:
            continue
        codes.update(re.findall(r"/item/main\.naver\?code=(\d{6})", h))
    return [f"{c}.KS" for c in sorted(codes)]   # KOSPI = .KS


def main():
    us = fetch_sp500()
    with open(US_OUT, "w") as f:
        f.write("\n".join(us) + "\n")
    print(f"universe_us.txt: {len(us)}개 (S&P500)")

    kr = fetch_kospi200()
    with open(KR_OUT, "w") as f:
        f.write("\n".join(kr) + "\n")
    print(f"universe_kr.txt: {len(kr)}개 (KOSPI200)")


if __name__ == "__main__":
    main()
