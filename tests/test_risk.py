import os
import pytest


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "risk.db"))
    import db
    db.init_db(); db.ensure_desk_accounts()
    import risk
    monkeypatch.setattr(risk, "RISK_GUARD_ENABLED", True)
    yield


def test_disabled_allows_everything(monkeypatch):
    import risk
    monkeypatch.setattr(risk, "RISK_GUARD_ENABLED", False)
    assert risk.precheck_buy("발굴주", "NVDA", 2_500_000) == (True, "", False)


def test_kill_switch_is_absolute_circuit_breaker():
    import risk
    ok, why, cb = risk.precheck_buy("발굴주", "NVDA", 2_500_000)
    assert ok is True                                    # 평상시 통과
    risk.set_kill_switch(True)
    ok, why, cb = risk.precheck_buy("발굴주", "NVDA", 2_500_000)
    assert ok is False and cb is True and "킬스위치" in why   # 절대 차단(오너 승인도 무시)
    assert risk.is_kill_switch_on() is True
    risk.set_kill_switch(False)
    assert risk.precheck_buy("발굴주", "NVDA", 2_500_000)[0] is True


def test_kill_switch_persists_across_reload(tmp_path, monkeypatch):
    import risk, db
    risk.set_kill_switch(True)
    assert db.get_risk_flag("kill_switch") == 1          # DB 영속 → 재시작에도 유지


def test_daily_loss_circuit_breaker(monkeypatch):
    import risk, db
    # 발굴주 시드 5,000만 · 오늘 -300만 실현손실(-6% < -5% 한도) → 당일 매수 중단
    monkeypatch.setattr(risk, "daily_realized_pnl", lambda a: -3_000_000 if a == "발굴주" else 0)
    ok, why, cb = risk.precheck_buy("발굴주", "NVDA", 2_500_000)
    assert ok is False and cb is True and "일일 손실" in why
    assert risk.precheck_buy("대형주", "AAPL", 5_000_000)[0] is True   # 다른 계좌는 무관


def test_data_anomaly_halt(monkeypatch):
    import risk, db
    # 오늘 price fetch 2성공 / 20실패 → 실패율 91% > 50% & 10건+ → 중단
    for _ in range(2):
        db.record_fetch("price", True)
    for _ in range(20):
        db.record_fetch("price", False)
    ok, why, cb = risk.precheck_buy("발굴주", "NVDA", 2_500_000)
    assert ok is False and cb is True and "데이터" in why


def test_position_count_limit(monkeypatch):
    import risk
    monkeypatch.setattr(risk, "MAX_POSITIONS", 2)
    import db
    db.buy_shared_position("A", "A", 10, 1_000_000, "t", "US", account="발굴주")
    db.buy_shared_position("B", "B", 10, 1_000_000, "t", "US", account="발굴주")
    ok, why, cb = risk.precheck_buy("발굴주", "C", 1_000_000)
    assert ok is False and cb is False and "포지션 수" in why   # 한도(절대차단 아님)


def test_single_weight_limit(monkeypatch):
    import risk
    monkeypatch.setattr(risk, "MAX_WEIGHT", 0.15)
    # 발굴주 자산 5,000만 · 단일 1,000만 매수 = 20% > 15% → 차단
    ok, why, cb = risk.precheck_buy("발굴주", "NVDA", 10_000_000)
    assert ok is False and cb is False and "비중" in why
    assert risk.precheck_buy("발굴주", "NVDA", 5_000_000)[0] is True   # 10%는 통과


def test_snapshot_reports_breakers():
    import risk
    risk.set_kill_switch(True)
    snap = risk.snapshot()
    assert snap["enabled"] is True and snap["breakers"]["kill_switch"] is True
    assert any(a["account"] == "발굴주" for a in snap["accounts"])
