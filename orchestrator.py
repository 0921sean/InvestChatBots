import json
import time
from datetime import datetime, timezone
from db import (
    create_round, complete_round, save_message, get_recent_messages,
    get_agent_state, save_agent_state, save_market_snapshot,
    get_latest_market_snapshot, save_consensus
)
from agents import call_agent
from prompts import (
    AGENT_ORDER, build_system_prompt, format_history,
    build_round_prompt, build_user_response_prompt,
    build_summary_prompt, build_evolution_prompt
)
from fetchers import fetch_market_data, fetch_news, build_market_summary

SUMMARY_ROTATION = list(AGENT_ORDER)
MARKET_REFRESH_HOURS = 2   # 시황 갱신 주기
CONSENSUS_EVERY_N_ROUNDS = 5  # N라운드마다 합의 체크
_round_counter = 0

def _get_summary_agent(index):
    return SUMMARY_ROTATION[index % len(SUMMARY_ROTATION)]

def _get_or_refresh_market_summary():
    """마지막 시황이 2시간 이내면 재사용, 아니면 새로 fetch."""
    snap = get_latest_market_snapshot()
    if snap:
        created = datetime.fromisoformat(snap["created_at"].replace(" ", "T"))
        # SQLite는 UTC naive로 저장되므로 naive 비교
        age_hours = (datetime.utcnow() - created).total_seconds() / 3600
        if age_hours < MARKET_REFRESH_HOURS:
            d = json.loads(snap["data"])
            return build_market_summary(d.get("data", {}), d.get("news", []))

    # 2시간 초과 or 첫 실행 → 새로 fetch
    data = fetch_market_data()
    news = fetch_news()
    save_market_snapshot(json.dumps({"data": data, "news": news}))
    return build_market_summary(data, news)

def run_round(market_summary=None):
    global _round_counter
    if market_summary is None:
        market_summary = _get_or_refresh_market_summary()

    round_id = create_round(market_summary[:200])
    save_message(round_id, "System", None, f"📊 {market_summary}")

    history = get_recent_messages(30)
    history_text = format_history(history)

    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_round_prompt(market_summary, history_text, agent_name)
        try:
            response = call_agent(agent_name, system, prompt)
        except Exception as e:
            response = f"[{agent_name} 응답 오류: {type(e).__name__}]"
        save_message(round_id, agent_name, None, response)
        history = get_recent_messages(30)
        history_text = format_history(history)
        time.sleep(1)

    complete_round(round_id)
    _round_counter += 1
    if _round_counter % CONSENSUS_EVERY_N_ROUNDS == 0:
        _detect_consensus(round_id)
    _maybe_update_evolution(round_id)

def handle_user_message(user_text):
    save_message(round_id=0, agent_name="User", model=None, content=user_text)

    history = get_recent_messages(30)
    history_text = format_history(history)

    summary_agent = _get_summary_agent(len(history))
    state = get_agent_state(summary_agent)
    system = build_system_prompt(summary_agent, state["evolution_notes"])
    try:
        summary = call_agent(summary_agent, system, build_summary_prompt(history_text, user_text))
    except Exception as e:
        summary = f"[요약 오류: {type(e).__name__}]"
    save_message(round_id=0, agent_name=summary_agent, model=None, content=summary)

    history = get_recent_messages(30)
    history_text = format_history(history)

    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_user_response_prompt(user_text, history_text, agent_name)
        try:
            response = call_agent(agent_name, system, prompt)
        except Exception as e:
            response = f"[{agent_name} 응답 오류: {type(e).__name__}]"
        save_message(round_id=0, agent_name=agent_name, model=None, content=response)
        history = get_recent_messages(30)
        history_text = format_history(history)
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
            save_consensus(round_id, result.strip())
    except Exception:
        pass

def _maybe_update_evolution(round_id):
    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        new_count = state["round_count"] + 1
        if new_count % 10 == 0:
            history = get_recent_messages(100)
            history_text = format_history(history)
            system = build_system_prompt(agent_name, state["evolution_notes"])
            new_notes = call_agent(
                agent_name, system,
                build_evolution_prompt(agent_name, history_text, state["evolution_notes"])
            )
            save_agent_state(agent_name, new_notes, new_count)
        else:
            save_agent_state(agent_name, state["evolution_notes"], new_count)
