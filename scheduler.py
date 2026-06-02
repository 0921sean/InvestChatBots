from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from orchestrator import (
    run_round,
    reset_kr_cycle, reset_us_cycle,
    run_watchlist_kr, run_watchlist_us,
)
from member_analyzer import run_member_analysis


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # ── 메인 사이클 (KST) ──────────────────────────────
    # 06시: 국장(KRX) 메인 사이클
    scheduler.add_job(reset_kr_cycle,
                      CronTrigger(hour=6, minute=0, day_of_week='mon-fri'),
                      id="main_cycle_kr", misfire_grace_time=300)
    # 18시: 미장(US) 메인 사이클 (미국장 22:30 개장 직전)
    scheduler.add_job(reset_us_cycle,
                      CronTrigger(hour=18, minute=0, day_of_week='mon-fri'),
                      id="main_cycle_us", misfire_grace_time=300)
    # ── 서브 사이클 (워치리스트) — KST 기준 ──────────────
    # 12시: 국장 워치리스트 (국장 메인 cycle_rest 이후)
    scheduler.add_job(run_watchlist_kr,
                      CronTrigger(hour=12, minute=0, day_of_week='mon-fri'),
                      id="sub_cycle_kr", misfire_grace_time=600)
    # 0시: 미장 워치리스트 (화·수·목·금만 — 월요일 0시는 6시 메인 직전이라 제외)
    scheduler.add_job(run_watchlist_us,
                      CronTrigger(hour=0, minute=0, day_of_week='tue,wed,thu,fri'),
                      id="sub_cycle_us", misfire_grace_time=600)
    # 18시 워치리스트는 폐지 (18시는 미장 메인 사이클이 차지).
    # 손절 체크(-20% 자동 매도)·보유 종목 점검은 각 메인 사이클 완료 직후 해당 시장만 자동 트리거
    # (_advance_stock → cycle_rest 진입 시 _post_cycle_checks(market) 실행).
    # 별도 cron job 없음. 수동 실행은 POST /api/holdings/review (force=True).
    scheduler.start()
    return scheduler
