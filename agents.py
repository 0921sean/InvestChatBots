import os
import time
import json
import subprocess
import openai
from google import generativeai as genai
from prompts import AGENT_PROFILES

RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 15  # 초
MAX_RESPONSE_CHARS = 350  # 이 글자 수 이후 첫 문장 끝에서 자르기

CLAUDE_CLI = os.path.expanduser("~/.local/bin/claude")

# Claude Code 토큰 소진 상태 관리
_claude_token_exhausted = False
_claude_retry_after = 0.0      # 이 시각 이후에 재시도
CLAUDE_RETRY_INTERVAL = 1800   # 30분마다 재시도


def is_claude_token_exhausted() -> bool:
    """토큰 소진 상태이고 아직 재시도 시각이 안 됐으면 True."""
    return _claude_token_exhausted and time.time() < _claude_retry_after


def _set_token_exhausted():
    global _claude_token_exhausted, _claude_retry_after
    _claude_token_exhausted = True
    _claude_retry_after = time.time() + CLAUDE_RETRY_INTERVAL


def _clear_token_exhausted():
    global _claude_token_exhausted
    _claude_token_exhausted = False


def trim_at_sentence(text: str) -> str:
    """MAX_RESPONSE_CHARS 이후 최초 문장 종결부호에서 자름. 짧으면 그대로."""
    if len(text) <= MAX_RESPONSE_CHARS:
        return text
    import re
    for m in re.finditer(r'[.!?…]\s*', text[MAX_RESPONSE_CHARS:]):
        cut = MAX_RESPONSE_CHARS + m.end()
        return text[:cut].strip()
    return text


def _call_claude_cli(system_prompt: str, user_prompt: str, timeout: int = 60, model: str = None) -> str:
    """claude CLI subprocess으로 호출 — Claude Code 구독 토큰 사용.
    `timeout`은 큰 prompt(섹터 합의 종합 등)에선 180~300초 권장.
    `model='haiku'`로 강제하면 큰 prompt에서도 빠른 응답 (합의 추출 권장)."""
    full_prompt = f"<system>\n{system_prompt}\n</system>\n\n{user_prompt}"
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # API 키 제거 → 구독 토큰 사용
    cmd = [CLAUDE_CLI, "--print", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    cmd += ["-p", full_prompt]
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=timeout,
        env=env,
        cwd="/tmp",  # 빈 디렉토리 → 프로젝트 컨텍스트 로딩 방지
    )
    exhausted_keywords = ("usage limit", "quota", "out of tokens",
                          "out of extra usage", "billing", "rate limit exceeded")
    if result.returncode != 0:
        stderr = result.stderr.lower()
        if any(k in stderr for k in exhausted_keywords):
            _set_token_exhausted()
            raise ClaudeTokenExhausted(f"Claude 토큰 소진: {result.stderr[:100]}")
        raise RuntimeError(f"claude CLI 오류: {result.stderr[:200]}")

    data = json.loads(result.stdout)
    result_text = data.get("result", "")

    # result 자체에 소진 메시지가 담기는 경우 처리
    if any(k in result_text.lower() for k in exhausted_keywords):
        _set_token_exhausted()
        raise ClaudeTokenExhausted(f"Claude 토큰 소진: {result_text[:100]}")

    if data.get("is_error"):
        raise RuntimeError(f"claude CLI 에러 응답: {data}")

    # 토큰 사용량 누적 (실패 시 조용히 무시 — 메인 로직 영향 없도록)
    try:
        usage = data.get("usage", {}) or {}
        from db import record_token_usage
        record_token_usage(
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_read=usage.get("cache_read_input_tokens", 0) or 0,
            cache_create=usage.get("cache_creation_input_tokens", 0) or 0,
            cost_usd=data.get("total_cost_usd", 0) or 0,
        )
    except Exception:
        pass

    _clear_token_exhausted()
    return result_text.strip()


class ClaudeTokenExhausted(Exception):
    pass




_openai_client = None
_gemini_configured = False


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


def call_agent(agent_name, system_prompt, user_prompt, timeout: int = 60, model: str = None, trim: bool = True):
    """timeout: claude CLI subprocess 시간 한도. 큰 prompt(합의 종합)는 180~300초 권장.
    model: 'haiku' / 'sonnet' / 'opus' 명시. None이면 CLI default (opus).
    trim: True면 MAX_RESPONSE_CHARS에서 문장단위로 자름(피드 브레비티). 종목 [결정]처럼
          끝줄이 중요한 응답은 False로 넘겨 잘림 방지."""
    _trim = trim_at_sentence if trim else (lambda x: x)
    profile = AGENT_PROFILES[agent_name]
    provider = profile["model_provider"]
    model_id = profile["model_id"]

    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            if provider == "claude":
                # Claude Code 구독 토큰 사용 (API 크레딧 아님)
                return _trim(_call_claude_cli(system_prompt, user_prompt, timeout=timeout, model=model))

            elif provider == "openai":
                client = _get_openai()
                resp = client.chat.completions.create(
                    model=model_id,
                    max_tokens=600,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    timeout=30,
                )
                return _trim(resp.choices[0].message.content)

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
                return _trim(resp.text)

            raise ValueError(f"Unknown provider: {provider}")

        except Exception as e:
            err = str(e).lower()
            is_rate = any(k in err for k in ("rate_limit", "tokens per", "too large", "requests per"))
            if is_rate and attempt < RATE_LIMIT_RETRIES - 1:
                time.sleep(RATE_LIMIT_WAIT)
                continue
            raise
