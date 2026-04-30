from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from orchestrator import run_round, verify_predictions, evaluate_positions


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(run_round, CronTrigger(hour=8, minute=0))
    scheduler.add_job(verify_predictions, CronTrigger(hour=9, minute=0))
    # 매일 오전 9시 30분 — 포지션 청산 평가
    scheduler.add_job(evaluate_positions, CronTrigger(hour=9, minute=30))
    scheduler.start()
    return scheduler
