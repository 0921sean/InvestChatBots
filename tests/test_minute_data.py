"""분봉 아카이브 로더 — 임시 파일로 순수 검증(외부 데이터 불필요)."""
import csv
import gzip
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import minute_data as md


def _write(tmp_path, sym, rows):
    d = tmp_path / "data_us"; d.mkdir(exist_ok=True)
    p = d / f"{sym}.csv.gz"
    with gzip.open(p, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(["dt", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    return str(d)


def _day_rows(date, n, base=100.0):
    """n개 분봉 — 시가 base, 종가 base+n, 고저 포함."""
    out = []
    for i in range(n):
        p = base + i
        out.append([f"{date}T23:{i%60:02d}:00.000+09:00", p, p + 0.5, p - 0.5, p + 0.2, 100])
    return out


def test_load_daily_resamples_ohlc(tmp_path, monkeypatch):
    rows = _day_rows("2026-08-10", 100, 100.0) + _day_rows("2026-08-11", 100, 200.0)
    monkeypatch.setattr(md, "DATA_DIR", _write(tmp_path, "TEST", rows))
    s = md.load_daily("TEST")
    assert s["dates"] == ["2026-08-10", "2026-08-11"]
    assert s["open"][0] == 100.0                    # 첫 봉 시가
    assert s["close"][0] == 199.2                   # 마지막 봉 종가(base+99+0.2)
    assert s["high"][0] == 199.5 and s["low"][0] == 99.5   # 장중 고저
    assert s["volume"][0] == 100 * 100


def test_load_daily_drops_partial_sessions(tmp_path, monkeypatch):
    # 30봉짜리 부분 세션은 결측으로 제외(기본 임계 60)
    rows = _day_rows("2026-08-10", 100) + _day_rows("2026-08-11", 30, 300.0)
    monkeypatch.setattr(md, "DATA_DIR", _write(tmp_path, "PART", rows))
    s = md.load_daily("PART")
    assert s["dates"] == ["2026-08-10"]
    s2 = md.load_daily("PART", min_bars_per_day=10)   # 임계 낮추면 포함
    assert len(s2["dates"]) == 2


def test_missing_symbol_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "DATA_DIR", str(tmp_path))
    assert md.load_daily("NOPE") == {}
    assert md.available("NOPE") is False


def test_load_many_filters_short_history(tmp_path, monkeypatch):
    long_rows, short_rows = [], []
    for i in range(300):
        long_rows += _day_rows(f"2026-01-{(i%28)+1:02d}", 70)     # 날짜 중복이나 그룹핑 검증용
    short_rows = _day_rows("2026-08-10", 70)
    d = _write(tmp_path, "LONG", long_rows)
    _write(tmp_path, "SHORT", short_rows)
    monkeypatch.setattr(md, "DATA_DIR", d)
    out = md.load_many_daily(["LONG", "SHORT"], min_days=5)
    assert "LONG" in out and "SHORT" not in out                    # 짧은 종목 제외


def test_intraday_extremes(tmp_path, monkeypatch):
    rows = _day_rows("2026-08-10", 100, 50.0)
    monkeypatch.setattr(md, "DATA_DIR", _write(tmp_path, "IX", rows))
    x = md.intraday_extremes("IX", "2026-08-10")
    assert x["open"] == 50.0 and x["high"] == 149.5 and x["low"] == 49.5
    assert md.intraday_extremes("IX", "2020-01-01") == {}


def test_list_symbols_and_coverage(tmp_path, monkeypatch):
    _write(tmp_path, "AAA", _day_rows("2026-08-10", 70))
    d = _write(tmp_path, "BBB", _day_rows("2026-08-10", 70))
    monkeypatch.setattr(md, "DATA_DIR", d)
    assert md.list_symbols() == ["AAA", "BBB"]
    cov = md.coverage()
    assert cov["symbols"] == 2 and cov["size_gb"] >= 0
