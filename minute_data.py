"""InvestData 분봉 아카이브 로더 — 로컬 1분봉을 백테스트용 시계열로.

형제 폴더 InvestData/data_us/{SYM}.csv.gz (dt,open,high,low,close,volume · KST ISO8601).
yfinance 없이 4.7년치 실데이터로 전략을 재검증하기 위한 어댑터.

핵심: backtest._fetch()와 동일한 반환 형식({'open','low','close','dates'})을 만들어
기존 백테스트 엔진(backtest_stock/portfolio_sim)을 코드 수정 없이 재사용한다.
"""
import csv
import gzip
import os
import logging
from collections import OrderedDict

logger = logging.getLogger("investchat")

DATA_DIR = os.getenv("MINUTE_DATA_DIR",
                     os.path.expanduser("~/Desktop/CSB/MyApps/InvestData/data_us"))
MIN_BARS_PER_DAY = 60          # 이 미만인 날은 결측으로 보고 일봉에서 제외(부분 세션 방지)


def available(symbol: str) -> bool:
    return os.path.exists(os.path.join(DATA_DIR, f"{symbol}.csv.gz"))


def list_symbols() -> list:
    """아카이브에 있는 전 종목."""
    try:
        return sorted(f[:-7] for f in os.listdir(DATA_DIR) if f.endswith(".csv.gz"))
    except Exception:
        return []


def load_daily(symbol: str, min_bars_per_day: int = MIN_BARS_PER_DAY) -> dict:
    """1분봉 → 일봉 리샘플. 반환 {'open','high','low','close','volume','dates'}(과거→현재).
    거래일별 첫 봉 시가·최고·최저·마지막 종가로 집계. 봉 수가 적은 날(결측)은 버린다."""
    path = os.path.join(DATA_DIR, f"{symbol}.csv.gz")
    if not os.path.exists(path):
        return {}
    days = OrderedDict()          # date -> [open, high, low, close, volume, bars]
    try:
        with gzip.open(path, "rt") as f:
            for row in csv.DictReader(f):
                d = (row.get("dt") or "")[:10]
                if not d:
                    continue
                try:
                    o, h, l, c = (float(row["open"]), float(row["high"]),
                                  float(row["low"]), float(row["close"]))
                    v = float(row.get("volume") or 0)
                except (TypeError, ValueError):
                    continue
                cur = days.get(d)
                if cur is None:
                    days[d] = [o, h, l, c, v, 1]
                else:
                    cur[1] = max(cur[1], h)
                    cur[2] = min(cur[2], l)
                    cur[3] = c                     # 마지막 봉 종가
                    cur[4] += v
                    cur[5] += 1
    except Exception as e:
        logger.warning(f"분봉 로드 실패 {symbol}: {e}")
        return {}
    out = {"open": [], "high": [], "low": [], "close": [], "volume": [], "dates": []}
    for d in sorted(days):
        o, h, l, c, v, bars = days[d]
        if bars < min_bars_per_day:                # 부분 세션(결측일) 제외
            continue
        out["dates"].append(d)
        out["open"].append(o); out["high"].append(h)
        out["low"].append(l); out["close"].append(c); out["volume"].append(v)
    return out if out["dates"] else {}


def load_many_daily(symbols, min_days: int = 260) -> dict:
    """여러 종목 일봉 로드. 데이터가 짧은 종목은 제외. {sym: series}."""
    out = {}
    for s in symbols:
        ser = load_daily(s)
        if ser and len(ser["dates"]) >= min_days:
            out[s] = ser
    return out


def intraday_extremes(symbol: str, date: str) -> dict:
    """특정 거래일의 장중 고저·시가·종가(슬리피지·장중 손절 검증용). 없으면 {}."""
    path = os.path.join(DATA_DIR, f"{symbol}.csv.gz")
    if not os.path.exists(path):
        return {}
    o = h = l = c = None
    try:
        with gzip.open(path, "rt") as f:
            for row in csv.DictReader(f):
                if (row.get("dt") or "")[:10] != date:
                    if o is not None:              # 해당 날짜 구간을 지나쳤으면 종료
                        break
                    continue
                try:
                    ro, rh, rl, rc = (float(row["open"]), float(row["high"]),
                                      float(row["low"]), float(row["close"]))
                except (TypeError, ValueError):
                    continue
                if o is None:
                    o, h, l = ro, rh, rl
                h = max(h, rh); l = min(l, rl); c = rc
    except Exception:
        return {}
    return {"open": o, "high": h, "low": l, "close": c} if o is not None else {}


def coverage() -> dict:
    """아카이브 요약(종목 수·용량) — 진단용."""
    syms = list_symbols()
    size = 0
    try:
        for f in os.listdir(DATA_DIR):
            if f.endswith(".csv.gz"):
                size += os.path.getsize(os.path.join(DATA_DIR, f))
    except Exception:
        pass
    return {"symbols": len(syms), "size_gb": round(size / 1e9, 2), "dir": DATA_DIR}
