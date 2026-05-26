from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from orchestrator import (
    run_round, evaluate_positions, reset_daily_cycle,
    run_telegram_watchlist, review_holdings,
)
from member_analyzer import run_member_analysis


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # 매일 6시: 사이클 리셋 후 시작
    scheduler.add_job(reset_daily_cycle, CronTrigger(hour=6, minute=0), misfire_grace_time=300)
    # 9시 30분: 손절 체크
    scheduler.add_job(evaluate_positions, CronTrigger(hour=9, minute=30), misfire_grace_time=300)
    # 월요일 8시 30분: 보유 종목 홀드/매도 점검 (메인 사이클 종료 후, 장 시작 전)
    scheduler.add_job(
        review_holdings,
        CronTrigger(day_of_week='mon', hour=8, minute=30),
        id="monday_holdings_review",
        misfire_grace_time=1800,  # 30분 이내 놓친 것도 실행
    )
    # 메인 완료 후 6시간 간격 (메인 진행 중이면 자동 skip, KST 기준)
    scheduler.add_job(
        run_telegram_watchlist,
        CronTrigger(hour='2,8,14,20', minute=0),
        id="telegram_watchlist",
        misfire_grace_time=600,  # 10분 이내 놓친 것도 실행
    )
    scheduler.start()
    return scheduler
