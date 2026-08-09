import os
import sqlite3
import pytest


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bak"))
    import importlib, ops as _ops
    importlib.reload(_ops)                                  # env 반영
    import db
    db.init_db(); db.ensure_desk_accounts()
    yield


def test_backup_creates_integrity_checked_copy(tmp_path):
    import ops
    r = ops.backup_db()
    assert r["ok"] is True and os.path.exists(r["file"])
    assert ops.db_integrity_ok(r["file"]) is True           # 백업본 자체가 무결
    assert r["size_mb"] >= 0


def test_backup_rotation_keeps_n(monkeypatch):
    import ops
    monkeypatch.setattr(ops, "BACKUP_KEEP", 3)
    for _ in range(6):
        # 스탬프 충돌 방지 위해 파일명 직접 회전 시뮬 대신 여러번 호출(초 단위 다름 보장 어려우니 목킹)
        pass
    # 회전 로직 단위 검증: 6개 놓고 rotate → 3개
    os.makedirs(ops.BACKUP_DIR, exist_ok=True)
    for i in range(6):
        open(os.path.join(ops.BACKUP_DIR, f"investchat-2026010{i}-000000.db"), "w").close()
    ops._rotate()
    assert len(ops._list_backups()) == 3                    # 최신 3개만
    assert ops._list_backups()[0].endswith("20260103-000000.db")  # 오래된 것부터 삭제


def test_safe_mode_flag_and_risk_absolute_block(monkeypatch):
    import ops, risk
    monkeypatch.setattr(risk, "RISK_GUARD_ENABLED", True)
    assert ops.is_safe_mode() is False
    ok, why, cb = risk.precheck_buy("발굴주", "NVDA", 2_500_000)
    assert ok is True
    monkeypatch.setattr("notifier.notify", lambda *a, **k: None)
    ops.set_safe_mode(True, "테스트")
    ok, why, cb = risk.precheck_buy("발굴주", "NVDA", 2_500_000)
    assert ok is False and cb is True and "세이프모드" in why  # 절대 차단
    ops.set_safe_mode(False)
    assert risk.precheck_buy("발굴주", "NVDA", 2_500_000)[0] is True


def test_startup_check_enters_safe_mode_on_bad_disk(monkeypatch):
    import ops
    monkeypatch.setattr("notifier.notify", lambda *a, **k: None)
    monkeypatch.setattr(ops, "disk_free_mb", lambda: 10)     # 10MB < 500 임계
    r = ops.startup_check()
    assert any("디스크" in p for p in r["problems"]) and ops.is_safe_mode() is True


def test_startup_check_clean_no_safe_mode(monkeypatch):
    import ops
    monkeypatch.setattr(ops, "disk_free_mb", lambda: 99999)
    r = ops.startup_check()
    assert r["problems"] == [] and ops.is_safe_mode() is False


def test_watchdog_recovers_from_transient(monkeypatch):
    import ops
    monkeypatch.setattr("notifier.notify", lambda *a, **k: None)
    monkeypatch.setattr(ops, "disk_free_mb", lambda: 10)
    ops.watchdog(); assert ops.is_safe_mode() is True       # 디스크 부족 → 세이프모드
    monkeypatch.setattr(ops, "disk_free_mb", lambda: 99999)
    ops.watchdog(); assert ops.is_safe_mode() is False      # 회복 → 자동 해제


def test_heartbeat_and_health(monkeypatch):
    import ops
    ops.beat()
    assert ops.heartbeat_age() < 5
    h = ops.health()
    assert h["ok"] is True and h["db_writable"] is True and h["disk_free_mb"] >= 0
