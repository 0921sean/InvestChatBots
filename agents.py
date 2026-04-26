import os
import anthropic
import openai
from google import generativeai as genai
from prompts import AGENT_PROFILES

_anthropic_client = None
_openai_client = None

def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client

def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client

def _get_gemini():
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    return genai

def call_agent(agent_name, system_prompt, user_prompt):
    profile = AGENT_PROFILES[agent_name]
    provider = profile["model_provider"]
    model_id = profile["model_id"]

    if provider == "claude":
        client = _get_anthropic()
        msg = client.messages.create(
            model=model_id,
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return msg.content[0].text

    elif provider == "openai":
        client = _get_openai()
        resp = client.chat.completions.create(
            model=model_id,
            max_tokens=300,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content

    elif provider == "gemini":
        g = _get_gemini()
        model = g.GenerativeModel(
            model_name=model_id,
            system_instruction=system_prompt,
        )
        resp = model.generate_content(user_prompt)
        return resp.text

    raise ValueError(f"Unknown provider: {provider}")
