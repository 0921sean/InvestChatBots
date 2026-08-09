"""신뢰성 계층 — 단일 노드/단일 파일 SPOF 완화.

DB 자동 백업(핫·무결성·회전·오프사이트) · 헬스체크 · 하트비트 · 부팅 자가진단 → 세이프모드.
설계: 인프라가 불건전하면(디스크 부족·DB 손상·스케줄러 정지) '매매를 멈추는' 것이 우선(fail-safe).
세이프모드는 risk 킬스위치와 별개인 'safe_mode' 플래그로, 회로차단기가 절대 차단으로 읽는다.
"""
import os
import shutil
import sqlite3
import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("investchat")

BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
OFFSITE_DIR = os.getenv("OFFSITE_DIR", "")            # 있으면 백업을 여기로도 복사(예: 동기화 폴더)
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))     # 최근 N개 보관(회전)
DISK_MIN_MB = int(os.getenv("DISK_MIN_MB", "500"))    # 여유 디스크 최소치(미만이면 세이프모드)
HEARTBEAT_STALE_SEC = int(os.getenv("HEARTBEAT_STALE_SEC", "5400"))  # 90분+ 무하트비트=정지 의심


def _db_path():
    return os.getenv("DB_PATH", "investchat.db")


def _kst():
    return datetime.now(timezone(timedelta(hours=9)))


# ── 하트비트 (스케줄러 살아있음 증거) ──
def beat():
    from db import set_ops_state
    set_ops_state("heartbeat", str(int(time.time())))


def heartbeat_age() -> float:
    from db import get_ops_state
    ts = get_ops_state("heartbeat")
    return (time.time() - int(ts)) if ts else 1e9


# ── 세이프모드 (인프라 이상 → 매매 중단, risk가 절대차단으로 읽음) ──
def is_safe_mode() -> bool:
    from db import get_risk_flag
    return bool(get_risk_flag("safe_mode"))


def set_safe_mode(on: bool, why: str = ""):
    from db import set_risk_flag
    was = is_safe_mode()
    set_risk_flag("safe_mode", 1 if on else 0)
    if on and not was:
        _notify("🛑 세이프모드 진입 — 매매 중단", f"인프라 이상: {why}. 신규매수를 자동 차단합니다.")
    elif not on and was:
        _notify("▶ 세이프모드 해제 — 매매 재개", "인프라 정상 복구.")


def _notify(title, body):
    try:
        from notifier import notify
        notify(title, body, priority="high", cooldown=1800)
    except Exception:
        pass


# ── 디스크 · DB 건전성 ──
def disk_free_mb() -> int:
    try:
        return int(shutil.disk_usage(os.path.abspath(_db_path()) or ".").free / (1024 * 1024))
    except Exception:
        return -1


def db_integrity_ok(path=None) -> bool:
    try:
        con = sqlite3.connect(path or _db_path())
        r = con.execute("PRAGMA integrity_check").fetchone()
        con.close()
        return bool(r) and r[0] == "ok"
    except Exception as e:
        logger.error(f"integrity_check 실패: {e}")
        return False


def db_writable() -> bool:
    try:
        from db import _conn
        with _conn() as con:
            con.execute("CREATE TABLE IF NOT EXISTS _ops_probe (x INTEGER)")
            con.execute("INSERT INTO _ops_probe (x) VALUES (1)")
            con.execute("DELETE FROM _ops_probe")
        return True
    except Exception:
        return False


# ── 백업 (핫 온라인 백업 → 무결성 검사 → 회전 → 오프사이트) ──
def backup_db() -> dict:
    """서버 가동 중에도 안전한 sqlite 온라인 백업. 무결성 검사 후 회전. 실패 시 오너 알림."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = _kst().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"investchat-{stamp}.db")
    try:
        src = sqlite3.connect(_db_path())
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)                              # 락 없이 일관 스냅샷(온라인 백업 API)
        dst.close(); src.close()
    except Exception as e:
        logger.error(f"백업 실패: {e}")
        _notify("⚠️ DB 백업 실패", str(e)[:120])
        return {"ok": False, "error": str(e)}
    if not db_integrity_ok(dest):                        # 손상된 백업은 무용 — 지우고 경고
        try:
            os.remove(dest)
        except Exception:
            pass
        _notify("⚠️ DB 백업 무결성 실패", "백업본이 손상됨 — 원본 점검 필요.")
        return {"ok": False, "error": "integrity failed"}
    if OFFSITE_DIR:                                      # 오프사이트(동기화 폴더 등)로도 복사
        try:
            os.makedirs(OFFSITE_DIR, exist_ok=True)
            shutil.copy2(dest, OFFSITE_DIR)
        except Exception as e:
            logger.warning(f"오프사이트 복사 실패: {e}")
    _rotate()
    from db import set_ops_state
    set_ops_state("last_backup", stamp)
    size_mb = round(os.path.getsize(dest) / (1024 * 1024), 1)
    return {"ok": True, "file": dest, "size_mb": size_mb, "kept": len(_list_backups())}


def _list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    return sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("investchat-") and f.endswith(".db"))


def _rotate():
    files = _list_backups()
    for f in files[:-BACKUP_KEEP] if len(files) > BACKUP_KEEP else []:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except Exception:
            pass


# ── 헬스 · 부팅 자가진단 ──
def health() -> dict:
    disk = disk_free_mb()
    dbw = db_writable()
    hb = heartbeat_age()
    from db import get_ops_state
    problems = []
    if not dbw:
        problems.append("DB 쓰기 불가")
    if 0 <= disk < DISK_MIN_MB:
        problems.append(f"디스크 부족({disk}MB)")
    if hb > HEARTBEAT_STALE_SEC:
        problems.append(f"스케줄러 정지 의심(하트비트 {int(hb//60)}분 전)")
    return {
        "ok": not problems, "problems": problems,
        "db_writable": dbw, "disk_free_mb": disk,
        "heartbeat_age_sec": int(hb) if hb < 1e8 else None,
        "last_backup": get_ops_state("last_backup"),
        "backups_kept": len(_list_backups()),
        "safe_mode": is_safe_mode(),
    }


def watchdog():
    """주기 점검(스케줄러가 호출) — 인프라 이상이면 세이프모드 진입, 회복되면 해제."""
    disk = disk_free_mb()
    problems = []
    if not db_writable():
        problems.append("DB 쓰기 불가")
    if 0 <= disk < DISK_MIN_MB:
        problems.append(f"디스크 여유 {disk}MB < {DISK_MIN_MB}MB")
    if problems:
        set_safe_mode(True, " · ".join(problems))
    elif is_safe_mode() and not _startup_problems():     # 부팅형 문제(손상 등) 아닌 경우만 자동 해제
        set_safe_mode(False)
    return {"problems": problems, "disk_free_mb": disk}


def _startup_problems():
    p = []
    if not db_integrity_ok():
        p.append("DB 무결성 손상")
    d = disk_free_mb()
    if 0 <= d < DISK_MIN_MB:
        p.append(f"디스크 부족({d}MB)")
    return p


def startup_check():
    """부팅 시 1회 — DB 무결성·디스크 점검. 문제 시 세이프모드로 시작(매매 안전 정지)."""
    beat()
    p = _startup_problems()
    if p:
        set_safe_mode(True, " · ".join(p) + " (부팅 자가진단)")
        logger.error(f"부팅 자가진단 실패 → 세이프모드: {p}")
    else:
        logger.info("부팅 자가진단 정상")
    return {"problems": p}
