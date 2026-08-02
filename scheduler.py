from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from aifund import run_largecap_cycle, run_discovery_cycle


def start_scheduler():
    """통일 데스크(대형주/발굴주) 자동 스케줄 — 컷오버(#98).
    committee 스케줄러는 은퇴(잡 제거). committee 코드·DB는 보존, 필요시 복구.
    각 슬롯은 NEW_DESK_ENABLED=False면 no-op(안전)."""
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # 06시: 대형주 데스크 (섹터 토론 → 종목 [결정] → Q 진입 타이밍). US 종가 직후 신선 일봉.
    scheduler.add_job(run_largecap_cycle,
                      CronTrigger(hour=6, minute=0, day_of_week='mon-fri'),
                      id="largecap_cycle", misfire_grace_time=600)
    # 12·18·24시: 발굴주 데스크 (A/S 발굴 → P/W/S OR게이트 즉시매수 + 보유 점검). 하루 3회.
    scheduler.add_job(run_discovery_cycle,
                      CronTrigger(hour="0,12,18", minute=0, day_of_week='mon-fri'),
                      id="discovery_cycle", misfire_grace_time=600)
    scheduler.start()
    return scheduler
