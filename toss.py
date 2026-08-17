"""토스증권 OpenAPI 시세 클라이언트 — 미장 현재가(최신 일봉 종가) 1순위 소스.

yfinance는 429 rate limit에 자주 막혀(라이브 장애 이력) 시세 소스를 이중화한다.
자격증명은 형제 폴더 InvestData/.env(TOSS_CLIENT_ID/SECRET)에서 읽는다 — 이 레포엔 키를 두지 않는다.

- 토큰: client_credentials, 만료 60초 전까지 캐시(스레드 안전)
- 시세: GET /api/v1/candles (interval=1d, before=ET 앵커) → 최신 봉 closePrice
- 레이트리밋: MARKET_DATA_CHART 5 TPS 규정 → 보수적으로 4 TPS로 간격 제어
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("investchat")

API = "https://openapi.tossinvest.com"
CRED_PATH = os.getenv("TOSS_ENV_PATH", os.path.expanduser(
    "~/Desktop/CSB/MyApps/InvestData/.env"))
_MIN_INTERVAL = 0.25                       # 4 TPS (규정 5 TPS 대비 여유)

_lock = threading.Lock()
_token = None
_expires_at = 0.0
_last_call = 0.0
_cred = None


def _creds():
    """InvestData/.env에서 자격증명 로드(1회). 없으면 (None, None)."""
    global _cred
    if _cred is not None:
        return _cred
    cid = os.getenv("TOSS_CLIENT_ID", "")
    csec = os.getenv("TOSS_CLIENT_SECRET", "")
    if not (cid and csec):
        try:
            for line in open(CRED_PATH):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "TOSS_CLIENT_ID":
                        cid = cid or v
                    elif k == "TOSS_CLIENT_SECRET":
                        csec = csec or v
        except Exception as e:
            logger.debug(f"토스 자격증명 로드 실패: {e}")
    _cred = (cid or None, csec or None)
    return _cred


def available() -> bool:
    cid, csec = _creds()
    return bool(cid and csec)


def _get_token():
    """액세스 토큰(캐시). 실패 시 None."""
    global _token, _expires_at
    with _lock:
        if _token and time.monotonic() < _expires_at - 60:
            return _token
        cid, csec = _creds()
        if not (cid and csec):
            return None
        body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "client_id": cid, "client_secret": csec}).encode()
        req = urllib.request.Request(API + "/oauth2/token", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                js = json.loads(r.read().decode())
            _token = js["access_token"]
            _expires_at = time.monotonic() + int(js.get("expires_in", 3600))
            return _token
        except Exception as e:
            logger.warning(f"토스 토큰 발급 실패: {e}")
            _token, _expires_at = None, 0.0
            return None


def _throttle():
    """전역 호출 간격 유지(레이트리밋 준수)."""
    global _last_call
    with _lock:
        gap = time.monotonic() - _last_call
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        _last_call = time.monotonic()


def _et_anchor_tomorrow() -> str:
    """내일 16:00 ET ISO8601 — 최신 일봉이 포함되도록 하는 before 커서."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
        return (datetime.now(tz) + timedelta(days=1)).replace(
            hour=16, minute=0, second=0, microsecond=0).isoformat()
    except Exception:
        return (datetime.utcnow() + timedelta(days=1)).replace(
            hour=20, minute=0, second=0, microsecond=0).isoformat() + "-04:00"


def _api_get(path, params, timeout=20):
    tok = _get_token()
    if not tok:
        return None
    _throttle()
    url = API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:                      # 토큰 만료 → 1회 재발급 후 재시도
            globals()["_token"] = None
            tok = _get_token()
            if tok:
                _throttle()
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
                                                           "Accept": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        return json.loads(r.read().decode())
                except Exception:
                    return None
        logger.debug(f"토스 API {e.code}: {path}")
        return None
    except Exception as e:
        logger.debug(f"토스 API 실패 {path}: {e}")
        return None


def get_price(symbol: str) -> float | None:
    """최신 일봉 종가(현재가 대용). 실패 시 None."""
    js = _api_get("/api/v1/candles", {"symbol": symbol, "interval": "1d", "count": 1,
                                      "adjusted": "true", "before": _et_anchor_tomorrow()})
    try:
        candles = ((js or {}).get("result") or {}).get("candles") or []
        return float(candles[0]["closePrice"]) if candles else None
    except Exception:
        return None


def get_closes(symbol: str, count: int = 260) -> list:
    """일봉 종가 시계열(과거→현재). 전략 신호용. 최대 200봉/요청이라 페이지네이션."""
    out, before = [], _et_anchor_tomorrow()
    while len(out) < count:
        js = _api_get("/api/v1/candles", {"symbol": symbol, "interval": "1d",
                                          "count": min(200, count - len(out)),
                                          "adjusted": "true", "before": before})
        res = (js or {}).get("result") or {}
        candles = res.get("candles") or []
        if not candles:
            break
        out.extend(float(c["closePrice"]) for c in candles)   # 최신→과거 순
        before = res.get("nextBefore")
        if not before:
            break
    return list(reversed(out))                                # 과거→현재
