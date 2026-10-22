"""반전봇(#236) — 네트워크·DB 없이 규칙 로직만 검증."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import reversal_bot as rb


def test_rsi14_known_values():
    # 단조 상승 → RSI 100
    assert rb._rsi14(list(range(1, 21))) == 100.0
    # 단조 하락 → RSI 0 근처
    assert rb._rsi14(list(range(40, 20, -1))) < 1.0
    # 길이 부족 → None
    assert rb._rsi14([1.0] * 10) is None


def test_et_anchor_dst():
    assert rb._et_anchor("2026-07-01", 9, 46).endswith("-04:00")   # EDT
    assert rb._et_anchor("2026-01-15", 9, 46).endswith("-05:00")   # EST


def _bars(date, vols, base_px=10.0):
    """09:31~09:45 분봉 15개 생성."""
    out = []
    for i, v in enumerate(vols):
        mm = 31 + i
        out.append({"ts": f"{date}T09:{mm:02d}:00+00:00", "open": base_px,
                    "close": base_px, "volume": v})
    return out


def test_get_minute_bars_converts_kst_to_et(monkeypatch):
    """회귀(2026-10-23): 토스는 분봉 ts를 KST로 준다(09:31 ET=22:31 KST).
    get_minute_bars 가 ET로 변환해야 호출부의 09:31~09:45 필터가 동작한다."""
    raw = {"result": {"candles": [
        {"timestamp": "2026-09-24T22:31:00.000+09:00", "openPrice": 10,
         "closePrice": 10, "volume": 500},
    ]}}
    monkeypatch.setattr(rb.toss, "_api_get", lambda *a, **k: raw)
    bars = rb.toss.get_minute_bars("X", "2026-09-24T09:46:00-04:00")
    assert bars[0]["ts"][11:16] == "09:31", f"KST→ET 변환 실패: {bars[0]['ts']}"


def test_or15_volume(monkeypatch):
    monkeypatch.setattr(rb.toss, "get_minute_bars",
                        lambda s, b, count=16: _bars("2026-10-21", [100] * 15))
    assert rb._or15_volume("X", "2026-10-21") == 1500
    # 봉 5개 미만 → 판정 불가
    monkeypatch.setattr(rb.toss, "get_minute_bars",
                        lambda s, b, count=16: _bars("2026-10-21", [100] * 3))
    assert rb._or15_volume("X", "2026-10-21") is None


def test_morning_entry_filters_and_ranks(monkeypatch, tmp_path):
    """갭/거래량서지 필터 + 상위 5개 선별이 규칙대로 동작하는지."""
    monkeypatch.setattr(rb, "REVERSAL_BOT_ENABLED", True)
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    # shortlist: 7개 후보 — 전부 5일 -10% 통과 상태. or_vol_base=1000.
    cands = []
    for i in range(7):
        cands.append({"symbol": f"S{i}", "ret5": -0.10 - i * 0.01, "prev_close": 10.0,
                      "rsi14": 30.0 - i, "dv20": 1e7, "or_vol_base": 1000.0})
    sl = tmp_path / "sl.json"
    sl.write_text(json.dumps({
        "made_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "candidates": cands}))
    monkeypatch.setattr(rb, "SHORTLIST_PATH", str(sl))

    # 분봉: S0 은 rvol 2배(미달), S1 은 갭 +50%(초과) → 탈락. 나머지 5개 통과(rvol 10배).
    def fake_bars(sym, before, count=16):
        if sym == "S0":
            return _bars(today, [2000 / 15] * 15)                    # rvol 2
        if sym == "S1":
            return _bars(today, [10000 / 15] * 15, base_px=15.0)     # 갭 +50%
        return _bars(today, [10000 / 15] * 15)                       # rvol 10
    monkeypatch.setattr(rb.toss, "get_minute_bars", fake_bars)

    bought = []
    monkeypatch.setattr(rb.db, "ensure_reversal_account", lambda: None)
    monkeypatch.setattr(rb.db, "get_shared_portfolio",
                        lambda a: {"balance": 10_000_000 - sum(b[1] for b in bought),
                                   "invested": sum(b[1] for b in bought)})
    monkeypatch.setattr(rb.db, "buy_shared_position",
                        lambda sym, code, px, amt, rsn, market, account:
                        (bought.append((sym, amt)) or (len(bought), None)))

    rb.morning_entry()

    syms = [b[0] for b in bought]
    assert "S0" not in syms and "S1" not in syms, "필터 미동작"
    assert len(syms) == 5, f"상위 5개만 매수해야 함: {syms}"
    # 각 매수 금액 = 자본의 ~20%
    assert abs(bought[0][1] - 2_000_000) < 1_000
    # 낙폭·저RSI 최상위(S6)가 1순위
    assert syms[0] == "S6"


def test_afternoon_exit_only_yesterday(monkeypatch):
    """오늘(ET) 산 포지션은 안 팔고, 어제 산 것만 판다."""
    monkeypatch.setattr(rb, "REVERSAL_BOT_ENABLED", True)
    now_et = datetime.now(ZoneInfo("America/New_York"))
    today_utc = now_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    pos = [
        {"id": 1, "symbol": "OLD", "opened_at": "2026-10-20 14:00:00"},   # 과거 → 매도
        {"id": 2, "symbol": "NEW", "opened_at": today_utc},               # 오늘 → 보유
    ]
    monkeypatch.setattr(rb.db, "get_open_positions", lambda account=None: pos)
    monkeypatch.setattr(rb.toss, "get_price", lambda s: 11.0)
    sold = []
    monkeypatch.setattr(rb.db, "sell_shared_position",
                        lambda pid, px, rsn: (sold.append(pid) or (0.0, None)))
    rb.afternoon_exit()
    assert sold == [1], f"어제 포지션만 매도해야 함: {sold}"
