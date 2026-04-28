import os
import time
import anthropic
import openai
from google import generativeai as genai
from prompts import AGENT_PROFILES

RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 15  # 초

_anthropic_client = None
_openai_client = None
_gemini_configured = False

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
    global _gemini_configured
    if not _gemini_configured:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        _gemini_configured = True
    return genai

def call_agent(agent_name, system_prompt, user_prompt):
    profile = AGENT_PROFILES[agent_name]
    provider = profile["model_provider"]
    model_id = profile["model_id"]

    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            if provider == "claude":
                client = _get_anthropic()
                msg = client.messages.create(
                    model=model_id,
                    max_tokens=400,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    timeout=30.0,
                )
                return msg.content[0].text

            elif provider == "openai":
                client = _get_openai()
                resp = client.chat.completions.create(
                    model=model_id,
                    max_tokens=400,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    timeout=30,
                )
                return resp.choices[0].message.content

            elif provider == "gemini":
                g = _get_gemini()
                model = g.GenerativeModel(
                    model_name=model_id,
                    system_instruction=system_prompt,
                )
                resp = model.generate_content(
                    user_prompt,
                    request_options={"timeout": 30},
                )
                return resp.text

            raise ValueError(f"Unknown provider: {provider}")

        except Exception as e:
            err = str(e).lower()
            is_rate = any(k in err for k in ("rate_limit", "tokens per", "too large", "requests per"))
            if is_rate and attempt < RATE_LIMIT_RETRIES - 1:
                time.sleep(RATE_LIMIT_WAIT)
                continue
            raise
