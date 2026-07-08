"""
트레이딩 규칙엔진용 브로드 유니버스 정적 리스트 생성기 (1회성/주기적 갱신).
결과를 universe_us.txt / universe_kr.txt 로 커밋 → 스캐너는 이 파일만 읽음(결정론적).

소스 = TradingView 스캐너 심볼셋(지수 구성종목). 로그인·키 불필요.
  · US = S&P500 ∪ 나스닥100
  · KR = KOSPI200(.KS) ∪ KOSDAQ150(.KQ)

  python build_universe.py    # 두 파일 갱신
"""
import json
import os
import re
import urllib.request

US_OUT = os.path.join(os.path.dirname(__file__), "universe_us.txt")
KR_OUT = os.path.join(os.path.dirname(__file__), "universe_kr.txt")


def _scan(market: str, symbolset: str, rng: int = 600) -> list[str]:
    body = {"columns": ["name"], "range": [0, rng],
            "symbols": {"symbolset": [symbolset]}}
    req = urllib.request.Request(
        f"https://scanner.tradingview.com/{market}/scan",
        data=json.dumps(body).encode(),
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
    return [row["d"][0] for row in (r.get("data") or [])]


def fetch_us() -> list[str]:
    syms = _scan("america", "SYML:SP;SPX") + _scan("america", "SYML:NASDAQ;NDX")
    # yfinance 표기: BRK.B → BRK-B
    return sorted({s.replace(".", "-").strip().upper() for s in syms if s})


def fetch_kr() -> list[str]:
    out: dict[str, str] = {}
    for code in _scan("korea", "SYML:KRX;KOSPI200"):
        if re.fullmatch(r"\d{6}", code):
            out[code] = f"{code}.KS"        # KOSPI = .KS
    for code in _scan("korea", "SYML:KRX;KOSDAQ150"):
        if re.fullmatch(r"\d{6}", code):
            out.setdefault(code, f"{code}.KQ")  # KOSDAQ = .KQ
    return sorted(out.values())


def main():
    us = fetch_us()
    with open(US_OUT, "w") as f:
        f.write("\n".join(us) + "\n")
    print(f"universe_us.txt: {len(us)}개 (S&P500 ∪ 나스닥100)")

    kr = fetch_kr()
    with open(KR_OUT, "w") as f:
        f.write("\n".join(kr) + "\n")
    print(f"universe_kr.txt: {len(kr)}개 (KOSPI200 ∪ KOSDAQ150)")


if __name__ == "__main__":
    main()
