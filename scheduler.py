from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from orchestrator import run_round, evaluate_positions, reset_daily_cycle, run_telegram_watchlist
from member_analyzer import run_member_analysis


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # 매일 6시: 사이클 리셋 후 시작
    scheduler.add_job(reset_daily_cycle, CronTrigger(hour=6, minute=0))
    # 9시 30분: 손절 체크
    scheduler.add_job(evaluate_positions, CronTrigger(hour=9, minute=30))
    # 메인 완료 후 고정 시각 8시~다음날 4시 (메인 진행 중이면 자동 skip, KST 기준)
    scheduler.add_job(
        run_telegram_watchlist,
        CronTrigger(hour='0,2,4,8,10,12,14,16,18,20,22', minute=0),
        id="telegram_watchlist",
    )
    scheduler.start()
    return scheduler
