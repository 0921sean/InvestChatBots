from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from orchestrator import run_round, verify_predictions


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(run_round, CronTrigger(hour=8, minute=0))
    # 매일 오전 9시 — 전날 예측을 현재 주가로 검증
    scheduler.add_job(verify_predictions, CronTrigger(hour=9, minute=0))
    scheduler.start()
    return scheduler
