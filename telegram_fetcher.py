"""
Telegram 투자방 메시지 수집기
- Telethon 동기(sync) 모드 사용
- 멤버 자격만 있으면 방장 불필요
- 여러 방 지원: TELEGRAM_GROUPS=t.me/group1,t.me/group2
- 메시지는 캐시해서 매 라운드마다 API 호출하지 않음
"""
import os
import logging
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("investchat.telegram")

CACHE_TTL_SECONDS = 1800    # 30분마다 갱신
MAX_MESSAGES = 60           # 방당 최대 수집 메시지 수
MIN_MSG_LEN = 10            # 이보다 짧은 메시지 필터링

_cache: dict = {}           # {group: {"messages": [...], "fetched_at": float}}


def _get_groups() -> list[str]:
    """환경변수에서 방 목록 파싱. TELEGRAM_GROUPS 우선, 없으면 TELEGRAM_GROUP."""
    raw = os.getenv("TELEGRAM_GROUPS") or os.getenv("TELEGRAM_GROUP", "")
    return [g.strip() for g in raw.split(",") if g.strip()]


def _get_client():
    try:
        from telethon.sync import TelegramClient
        api_id   = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        if not api_id or not api_hash:
            return None
        session_path = os.getenv("TELEGRAM_SESSION", "telegram_session")
        return TelegramClient(session_path, int(api_id), api_hash)
    except Exception as e:
        logger.debug(f"Telethon 클라이언트 생성 실패: {e}")
        return None


def _fetch_one(client, group: str, force: bool) -> list[dict]:
    """단일 그룹 메시지 수집 (클라이언트 연결 상태에서 호출)."""
    if not force and group in _cache:
        if time.time() - _cache[group]["fetched_at"] < CACHE_TTL_SECONDS:
            return _cache[group]["messages"]

    messages = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        for msg in client.iter_messages(group, limit=MAX_MESSAGES):
            if msg.date < cutoff:
                break
            text = msg.text or ""
            if len(text) < MIN_MSG_LEN:
                continue
            sender = "알 수 없음"
            try:
                if msg.sender:
                    first = getattr(msg.sender, "first_name", "") or ""
                    uname = getattr(msg.sender, "username", "")
                    sender = f"@{uname}" if uname else (first or "알 수 없음")
            except Exception:
                pass
            messages.append({
                "group": group,
                "sender": sender,
                "text": text[:300],
                "time": msg.date.strftime("%H:%M"),
            })
        messages.reverse()
        _cache[group] = {"messages": messages, "fetched_at": time.time()}
        logger.info(f"텔레그램 [{group}] {len(messages)}개 수집")
    except Exception as e:
        logger.warning(f"텔레그램 [{group}] 수집 실패: {e}")
        messages = _cache.get(group, {}).get("messages", [])
    return messages


def fetch_telegram_messages(group: str = None, force: bool = False) -> list[dict]:
    """
    모든 설정된 방(또는 특정 방)의 메시지를 수집해 합쳐서 반환.
    시간순 정렬.
    """
    groups = [group] if group else _get_groups()
    if not groups:
        return []

    client = _get_client()
    if client is None:
        return []

    all_messages = []
    try:
        with client:
            if not client.is_user_authorized():
                logger.warning("Telegram 세션 없음 — setup_telegram.py 먼저 실행 필요")
                return []
            for g in groups:
                msgs = _fetch_one(client, g, force)
                all_messages.extend(msgs)
    except Exception as e:
        logger.warning(f"Telethon 연결 오류: {e}")
        # 연결 실패 시 캐시 데이터로 대체
        for g in groups:
            all_messages.extend(_cache.get(g, {}).get("messages", []))

    # 시간순 정렬 (여러 방 메시지 섞기)
    all_messages.sort(key=lambda m: m["time"])
    return all_messages


def format_telegram_context(messages: list[dict], max_msgs: int = 30) -> str:
    """봇 프롬프트에 넣을 텔레그램 요약 문자열 생성."""
    if not messages:
        return ""

    recent = messages[-max_msgs:]
    groups = list(dict.fromkeys(m["group"] for m in recent))
    header = f"=== 텔레그램 투자방 최근 의견 ({len(recent)}개 / {len(groups)}개 방) ==="
    lines = [header]
    for m in recent:
        lines.append(f"[{m['time']}][{m['group'].split('/')[-1]}] {m['sender']}: {m['text']}")

    return "\n".join(lines)


def get_cached_context(max_msgs: int = 25) -> str:
    """캐시된 전체 방 메시지를 프롬프트용 문자열로 반환."""
    groups = _get_groups()
    if not groups:
        return ""

    all_messages = []
    for g in groups:
        all_messages.extend(_cache.get(g, {}).get("messages", []))

    if not all_messages:
        return ""

    all_messages.sort(key=lambda m: m["time"])
    return format_telegram_context(all_messages, max_msgs)
