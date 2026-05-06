"""
Telegram 투자방 메시지 수집기
- Telethon 동기(sync) 모드 사용
- 멤버 자격만 있으면 방장 불필요
- 메시지는 캐시해서 매 라운드마다 API 호출하지 않음
"""
import os
import logging
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("investchat.telegram")

# ── 캐시 설정 ──────────────────────────────────────────
_cache: dict = {}           # {group_id: {"messages": [...], "fetched_at": float}}
CACHE_TTL_SECONDS = 1800    # 30분마다 갱신
MAX_MESSAGES = 60           # 최대 수집 메시지 수
MIN_MSG_LEN = 10            # 이보다 짧은 메시지 필터링 (스티커·이모지 등 제외)


def _get_client():
    """Telethon 동기 클라이언트 반환. 세션 없으면 None."""
    try:
        from telethon.sync import TelegramClient
        api_id   = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        if not api_id or not api_hash:
            return None
        session_path = os.getenv("TELEGRAM_SESSION", "telegram_session")
        client = TelegramClient(session_path, int(api_id), api_hash)
        return client
    except Exception as e:
        logger.debug(f"Telethon 클라이언트 생성 실패: {e}")
        return None


def fetch_telegram_messages(group: str = None, force: bool = False) -> list[dict]:
    """
    텔레그램 그룹에서 최근 메시지 수집.
    group: 그룹 링크(t.me/xxx), username(@xxx), 또는 채팅 ID
    force: 캐시 무시하고 강제 갱신
    반환: [{"sender": str, "text": str, "time": str}, ...]
    """
    group = group or os.getenv("TELEGRAM_GROUP", "")
    if not group:
        logger.debug("TELEGRAM_GROUP 환경변수 미설정")
        return []

    # 캐시 확인
    cache_key = group
    if not force and cache_key in _cache:
        age = time.time() - _cache[cache_key]["fetched_at"]
        if age < CACHE_TTL_SECONDS:
            return _cache[cache_key]["messages"]

    client = _get_client()
    if client is None:
        return []

    try:
        with client:
            if not client.is_user_authorized():
                logger.warning("Telegram 세션 없음 — setup_telegram.py 먼저 실행 필요")
                return []

            messages = []
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

            for msg in client.iter_messages(group, limit=MAX_MESSAGES):
                if msg.date < cutoff:
                    break
                text = msg.text or ""
                if len(text) < MIN_MSG_LEN:
                    continue
                # 발신자 이름
                sender = "알 수 없음"
                try:
                    if msg.sender:
                        sender = getattr(msg.sender, "first_name", "") or ""
                        username = getattr(msg.sender, "username", "")
                        if username:
                            sender = f"@{username}" if not sender else sender
                except Exception:
                    pass

                messages.append({
                    "sender": sender,
                    "text": text[:300],
                    "time": msg.date.strftime("%H:%M"),
                })

            messages.reverse()  # 오래된 순으로

        _cache[cache_key] = {"messages": messages, "fetched_at": time.time()}
        logger.info(f"텔레그램 {len(messages)}개 메시지 수집 완료 ({group})")
        return messages

    except Exception as e:
        logger.warning(f"텔레그램 메시지 수집 실패: {e}")
        return _cache.get(cache_key, {}).get("messages", [])  # 실패 시 구캐시 반환


def format_telegram_context(messages: list[dict], max_msgs: int = 30) -> str:
    """봇 프롬프트에 넣을 텔레그램 요약 문자열 생성."""
    if not messages:
        return ""

    recent = messages[-max_msgs:]
    lines = [f"=== 텔레그램 투자방 최근 의견 ({len(recent)}개) ==="]
    for m in recent:
        lines.append(f"[{m['time']}] {m['sender']}: {m['text']}")

    return "\n".join(lines)


def get_cached_context(max_msgs: int = 25) -> str:
    """캐시된 텔레그램 메시지를 프롬프트용 문자열로 반환. 캐시 없으면 빈 문자열."""
    group = os.getenv("TELEGRAM_GROUP", "")
    if not group or group not in _cache:
        return ""
    messages = _cache[group].get("messages", [])
    return format_telegram_context(messages, max_msgs)
