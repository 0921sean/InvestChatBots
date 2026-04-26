from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from orchestrator import run_round


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(run_round, CronTrigger(hour=8, minute=0))
    scheduler.start()
    return scheduler
