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
    # ── 서브 사이클 (트레이딩 규칙엔진) — '개장 직후' (§6-B: 전일 완성 일봉 신호·당일 시가 체결) ──
    # 국장 개장 직후: 09:05 KST (개장 09:00)
    scheduler.add_job(run_watchlist_kr,
                      CronTrigger(hour=9, minute=5, day_of_week='mon-fri'),
                      id="sub_cycle_kr", misfire_grace_time=600)
    # 미장 개장 직후: 09:35 America/New_York (개장 09:30 ET) — 서머타임 자동(=22:35~23:35 KST)
    scheduler.add_job(run_watchlist_us,
                      CronTrigger(hour=9, minute=35, day_of_week='mon-fri', timezone='America/New_York'),
                      id="sub_cycle_us", misfire_grace_time=600)
    # 18시 워치리스트는 폐지 (18시는 미장 메인 사이클이 차지).
    # 손절 체크(-20% 자동 매도)·보유 종목 점검은 각 메인 사이클 완료 직후 해당 시장만 자동 트리거
    # (_advance_stock → cycle_rest 진입 시 _post_cycle_checks(market) 실행).
    # 별도 cron job 없음. 수동 실행은 POST /api/holdings/review (force=True).
    scheduler.start()
    return scheduler
