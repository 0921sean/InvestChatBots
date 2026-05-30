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
    # 서브 사이클 (워치리스트) — KST 기준
    # 0시: 화·수·목·금만 (월요일 0시는 6시 메인 시작 직전이라 제외)
    # 12·18시: 평일 (월~금 메인 cycle_rest 시 신호 수집)
    scheduler.add_job(
        run_telegram_watchlist,
        CronTrigger(hour=0, minute=0, day_of_week='tue,wed,thu,fri'),
        id="sub_cycle_midnight",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        run_telegram_watchlist,
        CronTrigger(hour='12,18', minute=0, day_of_week='mon-fri'),
        id="sub_cycle_day",
        misfire_grace_time=600,
    )
    # 손절 체크(-20% 자동 매도)·보유 종목 점검은 메인 사이클 완료 직후 자동 트리거
    # (_advance_stock → cycle_rest 진입 시 _post_cycle_checks 실행).
    # 별도 cron job 없음. 수동 실행은 POST /api/holdings/review (force=True).
    scheduler.start()
    return scheduler
