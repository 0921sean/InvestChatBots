import json
import logging
import time
import random
from datetime import datetime, timezone

logger = logging.getLogger("investchat")
from db import (
    create_round, complete_round, save_message, get_recent_messages,
    get_agent_state, save_agent_state, save_market_snapshot,
    get_latest_market_snapshot, save_consensus
)
from agents import call_agent
from prompts import (
    AGENT_ORDER, build_system_prompt, format_history,
    build_round_prompt, build_user_response_prompt,
    build_summary_prompt, build_evolution_prompt,
    build_position_summary
)
from fetchers import fetch_market_data, fetch_news, build_market_summary
from notifier import notify, is_credit_error

SUMMARY_ROTATION = list(AGENT_ORDER)
MARKET_REFRESH_HOURS = 2
CONSENSUS_EVERY_N_ROUNDS = 3
_round_counter = 0
_recent_speakers: list[str] = []   # 최근 발언자 추적 (공평성 보장)
_devil_silence = 0                 # Devil이 마지막 발언한 이후 라운드 수
DEVIL_MIN_SILENCE = 4              # 최소 4라운드는 조용히
DEVIL_MAX_SILENCE = 8              # 8라운드 침묵하면 강제 등장

def _get_summary_agent(index):
    return SUMMARY_ROTATION[index % len(SUMMARY_ROTATION)]

def _get_or_refresh_market_summary():
    """2시간 이내 시황은 재사용. 갱신 여부도 반환."""
    snap = get_latest_market_snapshot()
    if snap:
        created = datetime.fromisoformat(snap["created_at"].replace(" ", "T"))
        age_hours = (datetime.utcnow() - created).total_seconds() / 3600
        if age_hours < MARKET_REFRESH_HOURS:
            d = json.loads(snap["data"])
            return build_market_summary(d.get("data", {}), d.get("news", [])), False  # False = 캐시 사용

    data = fetch_market_data()
    news = fetch_news()
    save_market_snapshot(json.dumps({"data": data, "news": news}))
    return build_market_summary(data, news), True  # True = 새로 가져옴

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
    global _round_counter
    market_refreshed = False
    if market_summary is None:
        market_summary, market_refreshed = _get_or_refresh_market_summary()

    round_id = create_round(market_summary[:200])
    # 시황이 갱신됐을 때만 System 메시지 표시
    if market_refreshed:
        save_message(round_id, "System", None, f"📊 {market_summary}")

    all_history = get_recent_messages(20)    # 포지션 요약용 (더 많이)
    recent_history = all_history[-5:]        # 실제 대화 컨텍스트 (마지막 5개만)
    position_summary = build_position_summary(all_history)
    recent_text = format_history(recent_history)
    speakers = _pick_speakers()

    for agent_name in speakers:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_round_prompt(market_summary, position_summary, recent_text, agent_name)
        try:
            response = call_agent(agent_name, system, prompt)
        except Exception as e:
            if is_credit_error(e):
                notify(
                    f"⚠️ {agent_name} 크레딧 소진",
                    f"{agent_name}이 API 크레딧 부족으로 발언하지 못했습니다.\n"
                    f"({type(e).__name__})\n\n충전 후 계속됩니다.",
                    priority="high",
                )
            response = f"[{agent_name} 응답 오류: {type(e).__name__}]"
        save_message(round_id, agent_name, None, response)
        # 발언 후 포지션 요약 갱신
        all_history = get_recent_messages(20)
        recent_history = all_history[-5:]
        position_summary = build_position_summary(all_history)
        recent_text = format_history(recent_history)
        time.sleep(random.uniform(0.2, 0.6))

    complete_round(round_id)
    _round_counter += 1
    if _round_counter % CONSENSUS_EVERY_N_ROUNDS == 0:
        _detect_consensus(round_id)
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
        save_message(round_id=0, agent_name=agent_name, model=None, content=response)
        all_history = get_recent_messages(20)
        history_text = format_history(all_history[-5:])
        time.sleep(1)

def _detect_consensus(round_id):
    history = get_recent_messages(80)
    agent_msgs = [m for m in history if m["agent_name"] not in ("System", "User")]
    if len(agent_msgs) < 5:
        return
    conversation = "\n".join(f"{m['agent_name']}: {m['content']}" for m in agent_msgs[-30:])
    prompt = f"""다음은 5명의 투자 분석가 대화입니다:

{conversation}

투자 판단에 실제로 쓸 만한 합의가 있는지 분석하세요. 기준:
- 최소 3명 이상이 같은 방향으로 언급한 섹터/종목/테마
- 단순 언급이 아니라 "매수 긍정", "비중 확대", "유망" 등 투자 방향이 포함된 합의

있다면 아래 형식으로만 답하세요. 없으면 반드시 "합의 없음"만 출력:

📌 [섹터/종목/테마]
동의: [닉네임들]
근거: [공통 논리 1-2줄]
주의: [반론이나 리스크 한 줄, 없으면 생략]

최대 2개까지. 반드시 한국어로."""

    try:
        result = call_agent(
            "Tortoise",
            "당신은 투자 대화에서 실질적인 합의를 추출하는 분석가입니다. 확실한 합의만 기록하고, 애매한 언급은 무시하세요.",
            prompt
        )
        if result and "합의 없음" not in result and "📌" in result:
            content = result.strip()
            save_consensus(round_id, content)
            notify(
                "📌 InvestChat 합의 메모",
                content,
                priority="default",
            )
    except Exception:
        pass

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
