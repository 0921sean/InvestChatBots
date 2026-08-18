"""지수 벤치마크 정규화 검증 — 명세 §5 (같은 baseline 누적%, 절대레벨 안 섞음)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark as bm


def test_cum_is_relative_percent():
    assert bm._cum({"qqq": 100}, {"qqq": 110}, "qqq") == 10.0     # 절대레벨 110 아님, +10%
    assert bm._cum({"qqq": 200}, {"qqq": 180}, "qqq") == -10.0

def test_cum_none_when_missing_or_zero_base():
    assert bm._cum({"qqq": None}, {"qqq": 110}, "qqq") is None
    assert bm._cum({"qqq": 0}, {"qqq": 110}, "qqq") is None
    assert bm._cum({"qqq": 100}, {"qqq": None}, "qqq") is None

def test_compute_normalizes_each_series_from_baseline(monkeypatch):
    snaps = [
        {"date": "2026-07-01", "bot_nav": 100_000_000, "qqq": 500, "schd": 27, "kospi": 2600, "kosdaq": 900},
        {"date": "2026-07-09", "bot_nav": 102_000_000, "qqq": 525, "schd": 27.5, "kospi": 2626, "kosdaq": 882},
    ]
    monkeypatch.setattr(bm.db, "get_benchmark_snapshots", lambda: snaps)
    r = bm.compute_benchmark()
    assert r["available"] and r["baseline_date"] == "2026-07-01" and r["days"] == 2
    assert r["bot"] == 2.0        # 1억→1.02억
    assert r["qqq"] == 5.0        # 500→525
    assert r["kospi"] == 1.0
    assert r["kosdaq"] == -2.0
    # 절대레벨 안 섞음: bot은 %(2.0)이지 NAV(1.02억)가 아니다 — 지수와 같은 축(%)으로 비교
    assert r["bot"] < 100 and r["qqq"] < 100

def test_compute_empty_when_no_snapshot(monkeypatch):
    monkeypatch.setattr(bm.db, "get_benchmark_snapshots", lambda: [])
    assert bm.compute_benchmark() == {"available": False}

def test_compute_marks_missing_index_none(monkeypatch):
    snaps = [
        {"date": "a", "bot_nav": 100, "qqq": None, "schd": 10, "kospi": 100, "kosdaq": 100},
        {"date": "b", "bot_nav": 110, "qqq": 50, "schd": 11, "kospi": 100, "kosdaq": 100},
    ]
    monkeypatch.setattr(bm.db, "get_benchmark_snapshots", lambda: snaps)
    r = bm.compute_benchmark()
    assert r["qqq"] is None          # baseline 결측 → 데이터 없음(가짜 금지)
    assert r["bot"] == 10.0 and r["schd"] == 10.0 and r["kospi"] == 0.0


def test_bot_nav_uses_desk_accounts_not_committee(tmp_path, monkeypatch):
    """벤치마크 NAV는 라이브 데스크 기준 — 은퇴한 committee(main/sub)를 재면 안 된다."""
    import db, benchmark
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bm.db"))
    db.init_db(); db.ensure_desk_accounts()
    # committee 계좌에 큰 금액이 있어도 NAV에 안 잡혀야
    with db._conn() as con:
        con.execute("UPDATE shared_portfolio SET balance=999_000_000 WHERE id=1")
    monkeypatch.setattr(benchmark, "_position_price", lambda code, market: None)
    nav = benchmark.bot_combined_nav()
    seed = benchmark.desk_seed_total()
    assert nav == seed                       # 데스크 시드 그대로(committee 999M 미포함)
    assert seed == 110_000_000               # 대형주5천+발굴주5천+Q지수1천


def test_alpha_is_excess_over_index():
    import benchmark
    base = {"bot_nav": 100.0, "spy": 100.0, "qqq": 100.0}
    latest = {"bot_nav": 110.0, "spy": 105.0, "qqq": 112.0}
    assert benchmark._alpha(base, latest, "spy") == 5.0      # +10% vs +5% → +5%p
    assert benchmark._alpha(base, latest, "qqq") == -2.0     # 시장에 짐
    assert benchmark._alpha(base, {"bot_nav": None, "spy": 105.0}, "spy") is None


def test_bot_return_normalized_by_seed_excludes_capital_inflow():
    """계좌 신설로 자본이 유입돼도 수익률로 잡히면 안 된다(Q지수 개설 +1,000만 사례)."""
    import benchmark
    base = {"date": "2026-08-01", "bot_nav": 100_000_000.0}    # 시드 1억
    latest = {"date": "2026-08-10", "bot_nav": 110_000_000.0}  # 시드 1.1억(신설분 유입), 수익 0
    assert benchmark._cum(base, latest, "bot_nav") == 0.0       # 유입은 수익 아님
    latest2 = {"date": "2026-08-10", "bot_nav": 112_200_000.0}  # 시드 1.1억 대비 +2%
    assert benchmark._cum(base, latest2, "bot_nav") == 2.0
    # 지수는 정규화 없이 가격 비율 그대로
    assert benchmark._cum({"spy": 100.0}, {"spy": 105.0}, "spy") == 5.0


def test_seed_at_boundary():
    import benchmark
    assert benchmark._seed_at("2026-08-06") == 100_000_000.0
    assert benchmark._seed_at("2026-08-07") == 110_000_000.0    # Q지수 개설일
    assert benchmark._seed_at(None) is None
