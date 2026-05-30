from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from orchestrator import (
    run_round, reset_daily_cycle,
    run_telegram_watchlist,
)
from member_analyzer import run_member_analysis


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # 매일 6시: 사이클 리셋 후 시작
    scheduler.add_job(reset_daily_cycle, CronTrigger(hour=6, minute=0), misfire_grace_time=300)
    # 워치리스트: 0, 12, 18시 (메인 진행 중이면 자동 skip, KST 기준)
    scheduler.add_job(
        run_telegram_watchlist,
        CronTrigger(hour='0,12,18', minute=0),
        id="telegram_watchlist",
        misfire_grace_time=600,  # 10분 이내 놓친 것도 실행
    )
    # 손절 체크(-20% 자동 매도)·보유 종목 점검은 메인 사이클 완료 직후 자동 트리거
    # (_advance_stock → cycle_rest 진입 시 _post_cycle_checks 실행).
    # 별도 cron job 없음. 수동 실행은 POST /api/holdings/review (force=True).
    scheduler.start()
    return scheduler
