import json
from db import (
    create_round, complete_round, save_message, get_recent_messages,
    get_agent_state, save_agent_state, save_market_snapshot
)
from agents import call_agent
from prompts import (
    AGENT_ORDER, build_system_prompt, format_history,
    build_round_prompt, build_user_response_prompt,
    build_summary_prompt, build_evolution_prompt
)
from fetchers import fetch_market_data, fetch_news, build_market_summary

SUMMARY_ROTATION = list(AGENT_ORDER)

def _get_summary_agent(index):
    return SUMMARY_ROTATION[index % len(SUMMARY_ROTATION)]

def run_round(market_summary=None):
    if market_summary is None:
        data = fetch_market_data()
        news = fetch_news()
        save_market_snapshot(json.dumps({"data": data, "news": news}))
        market_summary = build_market_summary(data, news)

    round_id = create_round(market_summary[:200])
    save_message(round_id, "System", None, f"📊 {market_summary}")

    history = get_recent_messages(30)
    history_text = format_history(history)

    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_round_prompt(market_summary, history_text, agent_name)
        response = call_agent(agent_name, system, prompt)
        save_message(round_id, agent_name, None, response)
        history = get_recent_messages(30)
        history_text = format_history(history)

    complete_round(round_id)
    _maybe_update_evolution(round_id)

def handle_user_message(user_text):
    save_message(round_id=0, agent_name="User", model=None, content=user_text)

    history = get_recent_messages(30)
    history_text = format_history(history)

    summary_agent = _get_summary_agent(len(history))
    state = get_agent_state(summary_agent)
    system = build_system_prompt(summary_agent, state["evolution_notes"])
    summary = call_agent(summary_agent, system, build_summary_prompt(history_text, user_text))
    save_message(round_id=0, agent_name=summary_agent, model=None, content=summary)

    history = get_recent_messages(30)
    history_text = format_history(history)

    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_user_response_prompt(user_text, history_text, agent_name)
        response = call_agent(agent_name, system, prompt)
        save_message(round_id=0, agent_name=agent_name, model=None, content=response)
        history = get_recent_messages(30)
        history_text = format_history(history)

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
