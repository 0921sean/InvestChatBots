from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

from aifund import (run_largecap_cycle, run_discovery_cycle, run_bottleneck_curation,
                    run_macro_briefing, run_q_index_desk, run_risk_review, run_weekly_report,
                    run_workday, WORKDAY_ENABLED)


def _capture_benchmark():
    """지수 벤치마크 일일 스냅샷 — committee 스케줄러 은퇴 후 여기서 담당(데스크 게이트와 무관)."""
    from datetime import datetime, timezone, timedelta
    import benchmark
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    benchmark.capture_snapshot(today)


def start_scheduler():
    """통일 데스크 자동 스케줄 — 컷오버(#98).
    WORKDAY_ENABLED=True(기본 운영): 고정 시간표 폐지 — keeper(평일 05:30~21:30 매시 30분)가
      워크데이를 시작/재개(락으로 중복 방지). 출근 → 아침 블록 → 종일 스터디 라운드 → 22시 퇴근.
      토큰 소진·서버 재시작에도 keeper가 다음 시각에 자동 재출근.
    WORKDAY_ENABLED=False(롤백): 기존 고정 시간표(05:40 브리핑 … 0/12/18 발굴)로 복귀.
    committee 스케줄러는 은퇴(잡 제거). committee 코드·DB는 보존."""
    scheduler = BackgroundScheduler(
        timezone="Asia/Seoul",
        executors={"default": ThreadPoolExecutor(max_workers=6)},
        job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 600},
    )
    if WORKDAY_ENABLED:
        # keeper 하나가 전부 — run_workday 내부에서 브리핑·큐레이션·대형주·Q지수·리스크(전부
        # 하루 1회 가드·멱등) 후 발굴 스터디 라운드 반복. 05:30 첫 출근, 이후 매시 30분 재개 시도.
        scheduler.add_job(run_workday,
                          CronTrigger(hour='5-21', minute=30, day_of_week='mon-fri'),
                          id="workday_keeper", misfire_grace_time=1800)
    else:
        # ── 롤백용 고정 시간표 (WORKDAY_ENABLED=false) ──
        scheduler.add_job(run_largecap_cycle,
                          CronTrigger(hour=6, minute=0, day_of_week='mon-fri'),
                          id="largecap_cycle", misfire_grace_time=600)
        scheduler.add_job(run_discovery_cycle,
                          CronTrigger(hour="0,12,18", minute=0, day_of_week='mon-fri'),
                          id="discovery_cycle", misfire_grace_time=600)
        scheduler.add_job(run_bottleneck_curation,
                          CronTrigger(hour=5, minute=50, day_of_week='mon-fri'),
                          id="bottleneck_curation", misfire_grace_time=1800)
        scheduler.add_job(run_macro_briefing,
                          CronTrigger(hour=5, minute=40, day_of_week='mon-fri'),
                          id="macro_briefing", misfire_grace_time=1800)
        scheduler.add_job(run_q_index_desk,
                          CronTrigger(hour=6, minute=10, day_of_week='mon-fri'),
                          id="q_index_desk", misfire_grace_time=1800)
        scheduler.add_job(run_risk_review,
                          CronTrigger(hour=6, minute=20, day_of_week='mon-fri'),
                          id="risk_review", misfire_grace_time=1800)
    # 06시: 지수 벤치마크 스냅샷 (SPY·QQQ·코스피 등). 데스크 게이트와 무관하게 항상.
    scheduler.add_job(_capture_benchmark,
                      CronTrigger(hour=6, minute=0, day_of_week='mon-fri'),
                      id="benchmark_snapshot", misfire_grace_time=3600)
    # 토 09:00: 주간 성과 리포트(오너 전용, 결정적 계산·LLM 0) — 봇 채점 + 결재 부가가치 + 데이터 헬스.
    scheduler.add_job(run_weekly_report,
                      CronTrigger(hour=9, minute=0, day_of_week='sat'),
                      id="weekly_report", misfire_grace_time=3600)
    scheduler.start()
    return scheduler
