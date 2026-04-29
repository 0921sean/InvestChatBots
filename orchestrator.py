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
    save_prediction, get_unverified_predictions, mark_prediction_verified
)
from agents import call_agent
from prompts import (
    AGENT_ORDER, build_system_prompt, format_history,
    format_history_compact, build_round_prompt, build_user_response_prompt,
    build_summary_prompt, build_evolution_prompt,
    build_position_summary, build_summarize_prompt
)
from fetchers import fetch_market_data, fetch_news, fetch_technical_analysis, build_market_summary
from notifier import notify, is_credit_error, is_rate_limit

SUMMARY_ROTATION = list(AGENT_ORDER)
MARKET_REFRESH_HOURS = 2
CONSENSUS_EVERY_N_ROUNDS = 5
_round_counter = 0
_recent_speakers: list[str] = []
_devil_silence = 0
DEVIL_MIN_SILENCE = 4
DEVIL_MAX_SILENCE = 8
_pending_topic_shift = False   # 합의 후 다음 라운드에 주제 전환 신호
_last_consensus = ""           # 마지막으로 합의된 내용 (새 주제 안내에 사용)

def _get_summary_agent(index):
    return SUMMARY_ROTATION[index % len(SUMMARY_ROTATION)]

def _do_summarize(msg_id: int, agent_name: str, content: str):
    """백그라운드: Haiku로 요약 생성 후 DB 업데이트."""
    try:
        summary = call_agent(
            "Sigma",
            "투자 발언을 1-2문장으로 요약하는 전문가입니다. 종목/섹터, 투자 방향, 핵심 근거만 포함하세요.",
            build_summarize_prompt(agent_name, content),
        )
        with __import__("db")._conn() as con:
            con.execute("UPDATE messages SET summary=? WHERE id=?", (summary, msg_id))
    except Exception:
        pass

def _save_with_summary(round_id, agent_name, content):
    """메시지 저장 후 백그라운드에서 요약 생성."""
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
    save_market_snapshot(json.dumps({"data": data, "news": news, "ta": ta_data}))
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

def run_round(market_summary=None):
    global _round_counter, _pending_topic_shift
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
    # 주제 전환 직후엔 이전 대화 컨텍스트를 지워서 현대차 등 이전 주제에 묶이지 않게
    if this_round_is_shift:
        recent_history = []
        position_summary = ""
        recent_text = ""
    else:
        recent_history = all_history[-5:]
        position_summary = build_position_summary(all_history)
        recent_text = format_history_compact(recent_history)
    speakers = _pick_speakers()

    for i, agent_name in enumerate(speakers):
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_round_prompt(market_summary, position_summary, recent_text, agent_name,
                                    topic_shift=(i == 0 and this_round_is_shift))
        try:
            response = call_agent(agent_name, system, prompt)
        except Exception as e:
            if is_credit_error(e):
                notify(
                    f"⚠️ {agent_name} 크레딧 소진",
                    f"{agent_name} API 크레딧 부족.\n충전 후 계속됩니다.",
                    priority="high",
                )
            elif is_rate_limit(e):
                notify(
                    f"🔄 {agent_name} API 한도 초과",
                    f"크레딧 문제 아님 — 분당 요청/토큰 한도 초과.\n잠시 후 자동 재개됩니다.",
                    priority="default",
                )
            response = f"[{agent_name} 응답 오류: {type(e).__name__}]"
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
1. **5명 이상**이 긍정적으로 언급한 섹터·종목·테마 (매수, 비중확대, 유망, 주목할 만하다)
2. 위가 없으면 4명이 강하게 동의한 경우만 포함 (단순 언급 아닌 확실한 방향성)

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

    for pred in preds:
        symbol = pred["symbol"]
        current = current_prices.get(symbol)
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
