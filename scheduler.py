from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from aifund import run_largecap_cycle, run_discovery_cycle, run_bottleneck_curation


def _capture_benchmark():
    """지수 벤치마크 일일 스냅샷 — committee 스케줄러 은퇴 후 여기서 담당(데스크 게이트와 무관)."""
    from datetime import datetime, timezone, timedelta
    import benchmark
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    benchmark.capture_snapshot(today)


def start_scheduler():
    """통일 데스크(대형주/발굴주) 자동 스케줄 — 컷오버(#98).
    committee 스케줄러는 은퇴(잡 제거). committee 코드·DB는 보존, 필요시 복구.
    각 슬롯은 NEW_DESK_ENABLED=False면 no-op(안전)."""
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # 06시: 대형주 데스크 (섹터 토론 → 종목 [결정] → Q 진입 타이밍). US 종가 직후 신선 일봉.
    scheduler.add_job(run_largecap_cycle,
                      CronTrigger(hour=6, minute=0, day_of_week='mon-fri'),
                      id="largecap_cycle", misfire_grace_time=600)
    # 12·18·24시: 발굴주 데스크 (A/S 발굴 → P/W/S OR게이트 즉시매수 + 보유 점검). 하루 3회.
    scheduler.add_job(run_discovery_cycle,
                      CronTrigger(hour="0,12,18", minute=0, day_of_week='mon-fri'),
                      id="discovery_cycle", misfire_grace_time=600)
    # 05:50: S 병목 시드 큐레이션 (웹서치로 초크포인트 후보 조사 → pending 결재 큐). 06시 대형주 전에
    # pending을 준비해 출근길 결재. BOTTLENECK_CURATION_ENABLED=False면 no-op. 하루 1콜(구독 토큰).
    scheduler.add_job(run_bottleneck_curation,
                      CronTrigger(hour=5, minute=50, day_of_week='mon-fri'),
                      id="bottleneck_curation", misfire_grace_time=1800)
    # 06시: 지수 벤치마크 스냅샷 (SPY·QQQ·코스피 등). 데스크 게이트와 무관하게 항상.
    scheduler.add_job(_capture_benchmark,
                      CronTrigger(hour=6, minute=0, day_of_week='mon-fri'),
                      id="benchmark_snapshot", misfire_grace_time=3600)
    scheduler.start()
    return scheduler
