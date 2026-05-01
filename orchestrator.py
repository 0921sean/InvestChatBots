import json
import logging
import threading
import time
import random
from datetime import datetime, timezone

logger = logging.getLogger("investchat")
from db import (
    create_round, complete_round, save_message, get_recent_messages,
    get_agent_state, save_agent_state, save_market_snapshot,
    get_latest_market_snapshot, save_consensus,
    save_prediction, get_unverified_predictions, mark_prediction_verified,
    open_position, get_open_positions, close_position, complete_position,
    get_portfolio, get_shared_portfolio, update_shared_portfolio
)
from agents import call_agent
from prompts import (
    AGENT_ORDER, build_system_prompt, format_history,
    format_history_compact, build_round_prompt, build_user_response_prompt,
    build_summary_prompt, build_evolution_prompt, build_position_summary,
    build_summarize_prompt, is_investment_impossible,
    build_phase1_prompt, build_phase2_momentum_prompt, build_phase2_macro_prompt,
    build_phase3_risk_prompt, build_final_summary_prompt
)
from fetchers import fetch_market_data, fetch_news, fetch_technical_analysis, build_market_summary, fetch_stock_price
from discovery import discover_events
from notifier import notify, is_credit_error, is_rate_limit

SUMMARY_ROTATION = list(AGENT_ORDER)

def is_market_open() -> str:
    """현재 어느 시장이 열려있는지. 'KRX' | 'US' | 'NONE'"""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    weekday = now.weekday()  # 0=월 6=일
    if weekday >= 5:
        return "NONE"
    h, m = now.hour, now.minute
    # 국장: 9:00~15:30 KST
    if 9 <= h < 15 or (h == 15 and m <= 30):
        return "KRX"
    # 미장: 22:30~05:00 KST (다음날)
    if h >= 22 and m >= 30:
        return "US"
    if h < 5:
        return "US"
    return "NONE"
MARKET_REFRESH_HOURS = 2
CONSENSUS_EVERY_N_ROUNDS = 3
USER_IDLE_SECONDS = 300   # 사용자 5분 무응답 → 루프 재개
_round_counter = 0
_recent_speakers: list[str] = []
_devil_silence = 0
DEVIL_MIN_SILENCE = 4
DEVIL_MAX_SILENCE = 8
_user_active_until: float = 0.0
_agent_fail_count: dict = {}      # {agent_name: consecutive_fail_count}
AGENT_FAIL_ALERT = 3              # N번 연속 실패 시 알림
_pending_topic_shift = False
_stop_on_credit = False        # 크레딧 소진 시 전체 대화 정지 모드
_last_consensus = ""           # 마지막으로 합의된 내용 (새 주제 안내에 사용)

def _get_summary_agent(index):
    return SUMMARY_ROTATION[index % len(SUMMARY_ROTATION)]

def _do_summarize(msg_id: int, agent_name: str, content: str):
    """백그라운드: Haiku로 요약 생성 후 DB 업데이트."""
    try:
        summary = call_agent(
            "모멘텀 헌터",
            "투자 발언을 1-2문장으로 요약하는 전문가입니다. 종목/섹터, 투자 방향, 핵심 근거만. 한국어로.",
            build_summarize_prompt(agent_name, content),
        )
        with __import__("db")._conn() as con:
            con.execute("UPDATE messages SET summary=? WHERE id=?", (summary, msg_id))
    except Exception:
        pass

def _save_with_summary(round_id, agent_name, content):
    """메시지 저장 후 백그라운드 요약 생성."""
    from db import save_message as _save
    import threading
    msg_id = _save(round_id, agent_name, None, content)
    if content and not content.startswith("["):
        threading.Thread(
            target=_do_summarize, args=(msg_id, agent_name, content), daemon=True
        ).start()
    return msg_id

def _get_or_refresh_market_summary():
    """2시간 이내 시황은 재사용. 갱신 여부도 반환."""
    snap = get_latest_market_snapshot()
    if snap:
        created = datetime.fromisoformat(snap["created_at"].replace(" ", "T"))
        age_hours = (datetime.utcnow() - created).total_seconds() / 3600
        if age_hours < MARKET_REFRESH_HOURS:
            d = json.loads(snap["data"])
            return build_market_summary(d.get("data", {}), d.get("news", []), d.get("ta")), False  # False = 캐시 사용

    data = fetch_market_data()
    news = fetch_news()
    ta_data = fetch_technical_analysis()

    # 데이터 품질 체크 → 실패 시 알림
    price_ok = any(v.get("price") for v in data.values() if v)
    ta_ok = any(v is not None for v in ta_data.values())
    news_ok = len(news) > 0

    if not price_ok:
        notify("⚠️ 주가 데이터 없음",
               "Yahoo Finance 연결 실패 — 모든 종목 가격 수신 불가. 퀀트 판단 데이터 부족.",
               priority="high")
    if not ta_ok:
        notify("⚠️ TradingView 차트 데이터 없음",
               "RSI·MACD·이평선 수신 실패 — 트레이더·퀀트의 기술적 분석 데이터 부족.",
               priority="high")
    if not news_ok:
        notify("⚠️ 뉴스 수신 실패",
               "RSS 피드 연결 실패 — 최신 뉴스 없이 대화 진행 중.",
               priority="default")

    save_market_snapshot(json.dumps({"data": data, "news": news, "ta": ta_data}))
    threading.Thread(target=verify_predictions, daemon=True).start()
    return build_market_summary(data, news, ta_data), True

def _pick_speakers() -> list[str]:
    """
    3~4명 선택. 가중치 기반 랜덤 — 최근에 많이 말할수록 확률 감소.
    Devil은 별도 침묵 규칙.
    """
    global _recent_speakers, _devil_silence

    non_devil = [a for a in AGENT_ORDER if a != "Devil"]

    # 최근 발언 횟수 기반 역가중치 (적게 말한 에이전트 우선)
    counts = {a: _recent_speakers.count(a) for a in non_devil}
    max_count = max(counts.values()) if counts else 0
    weights = {a: (max_count - counts[a] + 1) for a in non_devil}

    # 가중치 비례 샘플링 (중복 없이 3~4명)
    pool = non_devil[:]
    chosen = []
    n = random.choice([3, 3, 4])  # 3명이 더 자주 (자연스러운 소규모 대화)
    for _ in range(min(n, len(pool))):
        total = sum(weights[a] for a in pool)
        r = random.uniform(0, total)
        cumul = 0
        for a in pool:
            cumul += weights[a]
            if r <= cumul:
                chosen.append(a)
                pool.remove(a)
                break

    # Devil 등장 판단
    devil_appears = False
    if _devil_silence >= DEVIL_MAX_SILENCE:
        devil_appears = True
    elif _devil_silence >= DEVIL_MIN_SILENCE:
        devil_appears = random.random() < 0.25

    if devil_appears:
        chosen.append("Devil")
        _devil_silence = 0
    else:
        _devil_silence += 1

    random.shuffle(chosen)
    _recent_speakers.extend(chosen)
    _recent_speakers = _recent_speakers[-30:]
    return chosen

def is_user_active() -> bool:
    return time.time() < _user_active_until

def run_round(market_summary=None):
    global _round_counter, _pending_topic_shift
    # 사용자가 최근 5분 내 발언했으면 이번 라운드 건너뜀
    if is_user_active():
        return
    market_refreshed = False
    if market_summary is None:
        market_summary, market_refreshed = _get_or_refresh_market_summary()

    round_id = create_round(market_summary[:200])
    if market_refreshed:
        save_message(round_id, "System", None, f"📊 {market_summary}")

    # 합의 후 주제 전환 — 이 라운드에서만 topic_shift=True
    this_round_is_shift = False
    if _pending_topic_shift:
        _pending_topic_shift = False
        this_round_is_shift = True
        save_message(round_id, "System", None,
                     "📌 합의 완료. 이제 다른 섹터·종목·테마로 논의를 전환합니다.")

    all_history = get_recent_messages(20)
    if this_round_is_shift:
        recent_history = []
        position_summary = ""
        recent_text = ""
    else:
        recent_history = all_history[-5:]
        position_summary = build_position_summary(all_history)
        recent_text = format_history_compact(recent_history)

    # Discovery 이벤트 (버그 수정: 항상 초기화 후 할당)
    event_text = ""
    try:
        event_text = discover_events(n=2) or ""
        if event_text and not market_refreshed:
            import random as _rand
            if _rand.random() > 0.5:
                event_text = ""
    except Exception:
        event_text = ""

    speakers = _pick_speakers()

    for i, agent_name in enumerate(speakers):
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        # 자기 최근 발언 3개 — 반복 방지
        self_recent = "\n---\n".join(
            m["content"][:100] for m in all_history
            if m["agent_name"] == agent_name
        )[-3*100:]
        prompt = build_round_prompt(market_summary, position_summary, recent_text, agent_name,
                                    topic_shift=(i == 0 and this_round_is_shift),
                                    self_recent=self_recent,
                                    event_text=event_text if i == 0 else "")
        try:
            response = call_agent(agent_name, system, prompt)
        except Exception as e:
            if is_credit_error(e):
                notify(f"⚠️ {agent_name} 크레딧 소진",
                       f"{agent_name} API 크레딧 부족. 충전 후 계속됩니다.", priority="high")
                if _stop_on_credit:
                    import main as _main
                    with _main._loop_lock:
                        _main._loop_running = False
                    notify("🛑 크레딧 소진으로 대화 정지",
                           f"{agent_name} 크레딧 부족 — 정지 모드 활성화로 대화를 멈췄습니다.", priority="high", cooldown=0)
            elif is_rate_limit(e):
                notify(f"🔄 {agent_name} API 한도 초과",
                       f"분당 요청/토큰 한도 초과. 잠시 후 자동 재개.", priority="default")
            # 연속 실패 카운트
            count = _agent_fail_count.get(agent_name, 0) + 1
            _agent_fail_count[agent_name] = count
            # 3번째, 이후 10번 단위마다 알림 (쿨다운 없이)
            if count == AGENT_FAIL_ALERT or (count > AGENT_FAIL_ALERT and count % 10 == 0):
                notify(f"🚨 {agent_name} {count}회 연속 오류",
                       f"{agent_name}이 {count}번 연속 응답 실패.\n({type(e).__name__})",
                       priority="high", cooldown=0)
            response = f"[{agent_name} 응답 오류: {type(e).__name__}]"
        else:
            _agent_fail_count[agent_name] = 0  # 성공 시 카운트 초기화
        _save_with_summary(round_id, agent_name, response)
        # 발언 후 포지션 요약 갱신 (compact 버전 사용)
        all_history = get_recent_messages(20)
        recent_history = all_history[-5:]
        position_summary = build_position_summary(all_history)
        recent_text = format_history_compact(recent_history)
        time.sleep(random.uniform(0.2, 0.6))

    complete_round(round_id)
    _round_counter += 1
    if _round_counter % CONSENSUS_EVERY_N_ROUNDS == 0:
        _detect_consensus(round_id)
    threading.Thread(target=_extract_predictions, args=(round_id,), daemon=True).start()
    _maybe_update_evolution(round_id)

def handle_user_message(user_text):
    global _user_active_until
    # 사용자 발언 → 5분간 AI 자율 대화 정지
    _user_active_until = time.time() + USER_IDLE_SECONDS
    save_message(round_id=0, agent_name="User", model=None, content=user_text)

    all_history = get_recent_messages(20)
    history_text = format_history(all_history[-5:])

    summary_agent = _get_summary_agent(len(all_history))
    state = get_agent_state(summary_agent)
    system = build_system_prompt(summary_agent, state["evolution_notes"])
    try:
        summary = call_agent(summary_agent, system, build_summary_prompt(history_text, user_text))
    except Exception as e:
        summary = f"[요약 오류: {type(e).__name__}]"
    save_message(round_id=0, agent_name=summary_agent, model=None, content=summary)

    all_history = get_recent_messages(20)
    history_text = format_history(all_history[-5:])

    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_user_response_prompt(user_text, history_text, agent_name)
        try:
            response = call_agent(agent_name, system, prompt)
        except Exception as e:
            response = f"[{agent_name} 응답 오류: {type(e).__name__}]"
        _save_with_summary(0, agent_name, response)
        all_history = get_recent_messages(20)
        history_text = format_history_compact(all_history[-5:])
        time.sleep(1)

def _detect_consensus(round_id):
    history = get_recent_messages(80)
    agent_msgs = [m for m in history if m["agent_name"] not in ("System", "User")]
    if len(agent_msgs) < 5:
        return
    conversation = "\n".join(f"{m['agent_name']}: {m['content']}" for m in agent_msgs[-30:])
    prompt = f"""다음은 투자 분석가들의 대화입니다:

{conversation}

실제 투자에 활용할 수 있는 **매수/관심 합의**를 찾으세요.

우선순위:
1. **3명 이상**이 긍정적으로 언급한 섹터·종목·테마 (매수, 비중확대, 유망, 주목할 만하다)
2. 위가 없으면 2명이 강하게 동의한 경우만 포함 (단순 언급 아닌 확실한 방향성)

매도 의견은 📌 형식이 아닌 ⚠️ 한 줄로만 (선택사항, 매우 강한 합의일 때만):
⚠️ 주의: [섹터/종목] — [이유 한 줄]

매수 합의가 있다면:
📌 [섹터/종목/테마]
동의: [닉네임들]
근거: [왜 지금 주목해야 하는지 1-2줄]
리스크: [한 줄, 없으면 생략]

매수 합의도 없고 강한 매도 합의도 없으면 반드시 "합의 없음"만 출력.
최대 2개. 반드시 한국어로."""

    try:
        result = call_agent(
            "Scout",
            "당신은 투자 대화에서 매수 관점의 합의를 추출하는 애널리스트입니다. 실제로 투자에 활용 가능한 아이디어만 기록하세요.",
            prompt
        )
        if result and "합의 없음" not in result and "📌" in result:
            content = result.strip()
            save_consensus(round_id, content)
            notify("📌 InvestChat 합의 메모", content, priority="default")
            global _pending_topic_shift, _last_consensus
            _pending_topic_shift = True
            _last_consensus = content
    except Exception:
        pass

def _extract_predictions(round_id):
    """라운드 직후 각 에이전트 발언에서 종목·방향 예측을 추출해 저장."""
    history = get_recent_messages(20)
    round_msgs = [m for m in history if m.get("round_id") == round_id
                  and m["agent_name"] not in ("System", "User")]
    if not round_msgs:
        return

    snap = get_latest_market_snapshot()
    prices = {}
    if snap:
        try:
            d = json.loads(snap["data"])
            for name, info in d.get("data", {}).items():
                if info and info.get("price"):
                    prices[name] = info["price"]
        except Exception:
            pass

    for msg in round_msgs:
        prompt = f"""다음 발언에서 특정 종목·지수·섹터에 대한 명확한 방향성 예측이 있으면 추출하세요.
없으면 "없음"만 출력.

발언: {msg['content']}

있으면 아래 형식으로만:
종목: [NVDA/KOSPI/반도체섹터 등]
방향: [상승/하락/중립]
근거: [한 줄]"""
        try:
            result = call_agent(
                "퀀트",
                "투자 발언에서 예측을 구조화 추출하는 시스템입니다.",
                prompt
            )
            if result and "없음" not in result and "종목:" in result:
                lines = {l.split(":")[0].strip(): ":".join(l.split(":")[1:]).strip()
                         for l in result.strip().split("\n") if ":" in l}
                symbol = lines.get("종목", "")
                direction = lines.get("방향", "")
                basis = lines.get("근거", "")
                if symbol and direction in ("상승", "하락", "중립"):
                    price = prices.get(symbol)
                    save_prediction(msg["agent_name"], symbol, direction, price, basis)
        except Exception:
            pass

def verify_predictions():
    """미검증 예측을 현재 주가와 비교해 맞았는지 확인 후 evolution notes 반영."""
    preds = get_unverified_predictions()
    if not preds:
        return

    snap = get_latest_market_snapshot()
    if not snap:
        return
    try:
        d = json.loads(snap["data"])
        current_prices = {k: v["price"] for k, v in d.get("data", {}).items()
                          if v and v.get("price")}
    except Exception:
        return

    # 종목명 유사 매칭 (NVDA주가 → NVDA 등)
    def _match_price(symbol):
        if symbol in current_prices:
            return current_prices[symbol]
        for key in current_prices:
            if key in symbol or symbol in key:
                return current_prices[key]
        return None

    for pred in preds:
        symbol = pred["symbol"]
        current = _match_price(symbol)
        if current is None:
            continue
        try:
            past = float(pred["price_at"])
        except Exception:
            continue

        change = (current - past) / past * 100
        if abs(change) < 1.5:  # ±1.5% 이내는 중립 처리
            continue

        actual = "상승" if change > 0 else "하락"
        correct = actual == pred["direction"]
        result = f"{'✅' if correct else '❌'} {pred['direction']} 예측 → 실제 {actual} ({change:+.1f}%)"
        mark_prediction_verified(pred["id"], result)

        # evolution notes에 피드백 반영
        state = get_agent_state(pred["agent_name"])
        feedback_line = f"[예측피드백] {pred['symbol']} {result} (근거: {pred['basis']})"
        notes = state["evolution_notes"]
        # 최근 피드백 5개만 유지
        existing = [l for l in notes.split("\n") if l.startswith("[예측피드백]")]
        existing.append(feedback_line)
        existing = existing[-5:]
        non_feedback = [l for l in notes.split("\n") if not l.startswith("[예측피드백]")]
        new_notes = "\n".join(non_feedback + existing).strip()
        save_agent_state(pred["agent_name"], new_notes, state["round_count"])

        # 알림
        notify(
            f"{'✅' if correct else '❌'} {pred['agent_name']} 예측 {'적중' if correct else '빗나감'}",
            f"{pred['symbol']}: {result}",
            priority="default"
        )

def _maybe_update_evolution(round_id):
    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        new_count = state["round_count"] + 1
        if new_count % 10 == 0:
            try:
                history = get_recent_messages(15)  # evolution도 30개로 제한
                history_text = format_history(history)
                system = build_system_prompt(agent_name, state["evolution_notes"])
                new_notes = call_agent(
                    agent_name, system,
                    build_evolution_prompt(agent_name, history_text, state["evolution_notes"])
                )
                save_agent_state(agent_name, new_notes, new_count)
            except Exception as e:
                logger.error(f"[evolution] {agent_name} 오류: {type(e).__name__}: {e}")
                save_agent_state(agent_name, state["evolution_notes"], new_count)
        else:
            save_agent_state(agent_name, state["evolution_notes"], new_count)

# ── 트레이드 파싱 ──────────────────────────────────────────
import re as _re
from datetime import datetime as _dt, timedelta as _td

def _parse_trade(agent_name: str, text: str, current_prices: dict):
    """발언에서 [TRADE] 블록을 파싱해 가상 포지션을 생성."""
    m = _re.search(r'\[TRADE\](.*?)(?:\n|$)', text, _re.DOTALL)
    if not m:
        return
    raw = m.group(1)
    def _field(key):
        fm = _re.search(rf'{key}:\s*([^\|]+)', raw)
        return fm.group(1).strip() if fm else ''

    symbol    = _field('종목') or _field('섹터')
    direction = _field('방향')
    period_s  = _field('목표기간')
    target_s  = _field('목표가')
    stop_s    = _field('손절가')
    ratio_s   = _field('비중')

    if not symbol or not direction:
        return

    # 현재 가격 조회
    entry = None
    for k, v in current_prices.items():
        if k.upper() in symbol.upper() or symbol.upper() in k.upper():
            entry = v
            break
    if entry is None or entry == 0:
        # 스냅샷에서 다시 한번 시도 (대소문자 무관)
        sym_upper = symbol.upper()
        for k, v in current_prices.items():
            if sym_upper in k.upper() or k.upper() in sym_upper:
                entry = v
                break
    if entry is None:
        entry = 0.0

    # 목표 기간 → 날짜
    weeks = _re.search(r'(\d+)\s*주', period_s)
    months = _re.search(r'(\d+)\s*개월', period_s)
    if weeks:
        target_date = (_dt.now() + _td(weeks=int(weeks.group(1)))).strftime('%Y-%m-%d')
    elif months:
        target_date = (_dt.now() + _td(days=int(months.group(1))*30)).strftime('%Y-%m-%d')
    else:
        target_date = (_dt.now() + _td(days=30)).strftime('%Y-%m-%d')

    # 금액 (비중 % 기반, 포트폴리오 ₩10,000,000 기준)
    ratio = float(_re.search(r'[\d.]+', ratio_s).group()) / 100 if _re.search(r'[\d.]+', ratio_s) else 0.1
    amount = 10_000_000 * min(ratio, 0.3)  # 최대 30%

    def _to_float(s):
        n = _re.search(r'[\d,.]+', s.replace(',', ''))
        return float(n.group().replace(',', '')) if n else 0.0

    open_position(
        agent_name=agent_name,
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        amount=amount,
        target_price=_to_float(target_s),
        stop_loss=_to_float(stop_s),
        target_date=target_date,
        reasoning=raw.strip(),
    )
    logger.info(f"[TRADE] {agent_name} → {symbol} {direction} ₩{amount:,.0f} until {target_date}")


def run_analysis(topic: str = None):
    """3-Phase 구조적 분석 — 특정 종목/섹터 분석 시 호출."""
    market_summary, _ = _get_or_refresh_market_summary()
    if not topic:
        # 토픽 없으면 최근 뉴스에서 주제 자동 추출
        snap = get_latest_market_snapshot()
        if snap:
            import json as _json
            d = _json.loads(snap["data"])
            news = d.get("news", [])
            topic = news[0]["title"] if news else "오늘의 시장 전반"
        else:
            topic = "오늘의 시장 전반"

    # 현재 가격 사전
    current_prices = {}
    try:
        snap = get_latest_market_snapshot()
        if snap:
            import json as _json
            d = _json.loads(snap["data"])
            for k, v in d.get("data", {}).items():
                if v and v.get("price"):
                    current_prices[k] = v["price"]
    except Exception:
        pass

    round_id = create_round(f"[분석] {topic[:100]}")
    save_message(round_id, "System", None, f"🔍 분석 시작: {topic}")

    # ── Phase 1: 펀더멘털 가디언 ─────────────────────────
    guardian = get_agent_state("펀더멘털 가디언")
    sys1 = build_system_prompt("펀더멘털 가디언", guardian["evolution_notes"])
    g_resp = call_agent("펀더멘털 가디언", sys1, build_phase1_prompt(market_summary, topic))
    _save_with_summary(round_id, "펀더멘털 가디언", g_resp)
    _parse_trade("펀더멘털 가디언", g_resp, current_prices)

    if is_investment_impossible(g_resp):
        save_message(round_id, "System", None, "🚫 투자 불가 판정으로 분석을 종료합니다.")
        complete_round(round_id)
        notify("🚫 투자불가 판정", f"[{topic}]\n{g_resp[:200]}", priority="default")
        return

    time.sleep(0.5)

    # ── Phase 2: 모멘텀 헌터 + 매크로 워처 ──────────────
    # 모멘텀 헌터 최근 발언 추출 (반복 방지용)
    all_msgs = get_recent_messages(30)
    recent_momentum = [m["content"][:120] for m in all_msgs
                       if m["agent_name"] == "모멘텀 헌터"][-3:]
    recent_momentum_str = "\n---\n".join(recent_momentum)

    m_state = get_agent_state("모멘텀 헌터")
    sys_m = build_system_prompt("모멘텀 헌터", m_state["evolution_notes"])
    m_resp = call_agent("모멘텀 헌터", sys_m,
                        build_phase2_momentum_prompt(market_summary, topic, g_resp, recent_momentum_str))
    _save_with_summary(round_id, "모멘텀 헌터", m_resp)
    _parse_trade("모멘텀 헌터", m_resp, current_prices)
    time.sleep(0.5)

    mac_state = get_agent_state("매크로 워처")
    sys_mac = build_system_prompt("매크로 워처", mac_state["evolution_notes"])
    mac_resp = call_agent("매크로 워처", sys_mac, build_phase2_macro_prompt(market_summary, topic, g_resp))
    _save_with_summary(round_id, "매크로 워처", mac_resp)
    _parse_trade("매크로 워처", mac_resp, current_prices)
    time.sleep(0.5)

    # ── Phase 3: 리스크 어드바이저 + 최종 요약 ──────────
    r_state = get_agent_state("리스크 어드바이저")
    sys_r = build_system_prompt("리스크 어드바이저", r_state["evolution_notes"])
    r_resp = call_agent("리스크 어드바이저", sys_r,
                        build_phase3_risk_prompt(topic, g_resp, m_resp, mac_resp))
    _save_with_summary(round_id, "리스크 어드바이저", r_resp)
    time.sleep(0.5)

    # 최종 요약 테이블 (GPT-4o-mini로 저렴하게)
    summary_resp = call_agent("모멘텀 헌터",
                              "당신은 투자 분석 요약 전문가입니다. 표 형식으로 정리하세요.",
                              build_final_summary_prompt(topic, g_resp, m_resp, mac_resp, r_resp))
    save_message(round_id, "System", None, f"📊 분석 결과\n\n{summary_resp}")
    complete_round(round_id)
    notify("📊 분석 완료", f"[{topic}]\n{summary_resp[:300]}", priority="default")


def evaluate_positions():
    """목표 기간 도달 또는 손절가 터치 포지션을 청산하고 evolution notes에 반영."""
    open_pos = get_open_positions()
    if not open_pos:
        return

    snap = get_latest_market_snapshot()
    if not snap:
        return
    try:
        import json as _json
        d = _json.loads(snap["data"])
        prices = {k: v["price"] for k, v in d.get("data", {}).items() if v and v.get("price")}
    except Exception:
        prices = {}

    today = _dt.now().strftime('%Y-%m-%d')

    for pos in open_pos:
        symbol = pos["symbol"]
        # 스냅샷에서 먼저 찾기
        current = None
        for k, v in prices.items():
            if k.upper() in symbol.upper() or symbol.upper() in k.upper():
                current = v
                break
        # 스냅샷에 없으면 실시간 조회
        if current is None:
            current = fetch_stock_price(symbol)
        if current is None:
            continue
        # 진입가 0이면 현재가로 업데이트
        if pos["entry_price"] == 0:
            from db import _conn
            with _conn() as con:
                con.execute("UPDATE virtual_positions SET entry_price=? WHERE id=?",
                           (current, pos["id"]))
            continue

        target_date_reached = pos["target_date"] and today >= pos["target_date"]
        stop_hit = (pos["stop_loss"] and pos["direction"] == "매수" and current <= pos["stop_loss"]) or \
                   (pos["stop_loss"] and pos["direction"] == "공매도" and current >= pos["stop_loss"])

        if not target_date_reached and not stop_hit:
            continue

        reason = "목표 기간 도달" if target_date_reached else "손절가 도달"
        result = close_position(pos["id"], current, reason)
        if not result:
            continue

        pnl = result["pnl"]
        pnl_pct = result["pnl_pct"]
        win = pnl >= 0
        emoji = "✅" if win else "❌"

        feedback = (f"[포지션 결과] {pos['symbol']} {pos['direction']} | "
                    f"{emoji} {pnl_pct:+.1f}% (₩{pnl:+,.0f}) | {reason}")

        # evolution notes에 피드백 추가
        state = get_agent_state(pos["agent_name"])
        notes = state["evolution_notes"] or ""
        feedback_lines = [l for l in notes.split("\n") if l.startswith("[포지션 결과]")]
        feedback_lines.append(feedback)
        feedback_lines = feedback_lines[-5:]  # 최근 5개만
        other_lines = [l for l in notes.split("\n") if not l.startswith("[포지션 결과]")]
        new_notes = "\n".join(other_lines + feedback_lines).strip()
        save_agent_state(pos["agent_name"], new_notes, state["round_count"])

        notify(f"{emoji} {pos['agent_name']} 포지션 청산",
               f"{pos['symbol']} {pos['direction']}\n{pnl_pct:+.1f}% (₩{pnl:+,.0f})\n{reason}",
               priority="high" if not win else "default", cooldown=0)
        logger.info(f"[POSITION] {pos['agent_name']} {symbol} closed: {pnl_pct:+.1f}%")
