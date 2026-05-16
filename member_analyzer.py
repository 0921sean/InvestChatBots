"""
피델리티 텔레그램 그룹 멤버 투자 스타일 분석기
- 주기적으로 메시지 수집 → 발화자별 누적
- 충분한 데이터(30개+) 쌓이면 Claude로 스타일 분석
- 분석 완료 시 ntfy 알림
"""
import os
import json
import time
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("investchat.member")

FIDELITY_GROUP_ID = -4669026422
MIN_MESSAGES_FOR_ANALYSIS = 30   # 분석 시작 최소 메시지 수
NOTIFY_THRESHOLD = 50            # 이 수 이상이면 파악 완료 알림
DB_PATH = os.path.join(os.path.dirname(__file__), 'investchat.db')

# 분석에서 제외할 봇/시스템 계정
SKIP_NAMES = {'Telegram', 'BotFather', None}


def _conn():
    return sqlite3.connect(DB_PATH)


def collect_fidelity_messages(limit: int = 500) -> dict[str, list[str]]:
    """피델리티 그룹 메시지를 발화자별로 수집 (최대 limit개)."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
        from telethon.sync import TelegramClient

        api_id = int(os.getenv('TELEGRAM_API_ID'))
        api_hash = os.getenv('TELEGRAM_API_HASH')
        session = os.path.join(os.path.dirname(__file__), 'telegram_session')

        by_member: dict[str, list[str]] = {}

        with TelegramClient(session, api_id, api_hash) as client:
            msgs = client.get_messages(FIDELITY_GROUP_ID, limit=limit)
            for m in msgs:
                if not m.text or len(m.text) < 5:
                    continue
                if not m.sender:
                    continue
                first = getattr(m.sender, 'first_name', '') or ''
                last = getattr(m.sender, 'last_name', '') or ''
                name = f"{first} {last}".strip() or getattr(m.sender, 'username', '') or '알수없음'
                if name in SKIP_NAMES:
                    continue
                by_member.setdefault(name, []).append(m.text[:300])

        return by_member
    except Exception as e:
        logger.debug(f"피델리티 메시지 수집 오류: {e}")
        return {}


def _update_member_messages(name: str, group_id: str, new_msgs: list[str]):
    """DB에 멤버 메시지 수 누적."""
    with _conn() as con:
        con.execute("""
            INSERT INTO member_profiles (name, group_id, messages_collected, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET
                messages_collected = messages_collected + ?,
                updated_at = CURRENT_TIMESTAMP
        """, (name, group_id, len(new_msgs), len(new_msgs)))


def _get_member(name: str) -> dict | None:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM member_profiles WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None


def _save_profile(name: str, profile: str):
    with _conn() as con:
        con.execute("UPDATE member_profiles SET profile=?, updated_at=CURRENT_TIMESTAMP WHERE name=?",
                    (profile, name))


def _mark_notified(name: str):
    with _conn() as con:
        con.execute("UPDATE member_profiles SET notified=1 WHERE name=?", (name,))


def _get_bot_name_for_member(member_name: str) -> str | None:
    """멤버 이름 → 봇 이름 매핑 조회."""
    with _conn() as con:
        row = con.execute(
            "SELECT bot_name FROM member_profiles WHERE name=?", (member_name,)
        ).fetchone()
        return row[0] if row else None


def check_bot_overlap(new_name: str, new_profile: str) -> None:
    """신규 봇과 기존 봇의 포지션 겹침 여부 체크 → ntfy 알림."""
    from agents import _call_claude_cli, is_claude_token_exhausted
    from notifier import notify
    if is_claude_token_exhausted():
        return
    try:
        from prompts import AGENT_PROFILES
        # 자기 자신(멤버→봇 매핑) 제외
        own_bot_name = _get_bot_name_for_member(new_name)
        existing = "\n\n".join(
            f"[{n}] {p['description']}\n{p['system'][:300]}"
            for n, p in AGENT_PROFILES.items()
            if n != new_name and n != own_bot_name
        )
        prompt = f"""기존 투자 AI봇들과 신규 봇의 투자 스타일이 겹치는지 판단해주세요.

=== 기존 봇들 ===
{existing}

=== 신규 봇: {new_name} ===
{new_profile[:500]}

겹치는 봇이 있으면: "⚠️ [기존봇이름]와 포지션 겹침: 이유 한 줄"
겹치지 않으면: "✅ 포지션 독립적"
딱 한 줄로만 답하세요."""

        result = _call_claude_cli("투자 봇 포지션 분석가", prompt)
        if "겹침" in result or "⚠️" in result:
            notify(f"⚠️ 봇 포지션 겹침: {new_name}", result.strip(), priority="default", cooldown=0)
            logger.info(f"[{new_name}] 포지션 겹침 감지: {result}")
    except Exception as e:
        logger.debug(f"포지션 겹침 체크 오류: {e}")


def analyze_member_style(name: str, messages: list[str]) -> str:
    """Claude CLI로 멤버 투자 스타일 분석."""
    from agents import _call_claude_cli
    sample = '\n'.join(f'- {m}' for m in messages[:50])
    prompt = f"""다음은 투자 텔레그램 채팅방에서 "{name}"이 한 발언들입니다.

{sample}

이 사람의 투자 스타일을 아래 항목으로 간결하게 분석해주세요 (각 2-3줄):
1. 주요 관심 섹터/종목
2. 투자 철학 (가치투자/성장투자/트레이딩 등)
3. 분석 방식 (기술적/펀더멘털/매크로 등)
4. 성격/말투 특징
5. 봇 캐릭터로 만든다면 어떤 스타일?"""

    return _call_claude_cli("투자 스타일 분석 전문가입니다.", prompt)


def _process_members(by_member: dict[str, list[str]]):
    """수집된 발화자별 메시지로 프로필 업데이트 + 알림."""
    from agents import is_claude_token_exhausted
    from notifier import notify

    for name, msgs in by_member.items():
        _update_member_messages(name, str(FIDELITY_GROUP_ID), msgs)
        member = _get_member(name)
        if not member:
            continue

        total = member['messages_collected']

        if total >= MIN_MESSAGES_FOR_ANALYSIS and not member.get('profile'):
            if is_claude_token_exhausted():
                continue
            try:
                profile = analyze_member_style(name, msgs)
                _save_profile(name, profile)
                logger.info(f"[{name}] 프로필 분석 완료")
                # 봇으로 추가될 예정이면 포지션 겹침 체크
                check_bot_overlap(name, profile)
            except Exception as e:
                logger.debug(f"프로필 분석 오류 {name}: {e}")
                continue

        if total >= NOTIFY_THRESHOLD and not member.get('notified') and member.get('profile'):
            _mark_notified(name)
            profile_summary = (member.get('profile') or '')[:300]
            notify(
                f"👤 피델리티 멤버 파악 완료: {name}",
                f"메시지 {total}개 분석\n\n{profile_summary}\n\n봇 추가 시 bot_name을 등록해주세요.",
                priority="default", cooldown=0
            )


def run_member_analysis():
    """독립 실행 — 텔레그램 직접 수집 후 분석."""
    from agents import is_claude_token_exhausted
    if is_claude_token_exhausted():
        return
    by_member = collect_fidelity_messages(limit=500)
    if by_member:
        _process_members(by_member)


def run_member_analysis_from_cache():
    """워치리스트와 함께 실행 — 이미 열린 세션의 캐시 재활용.
    telegram_fetcher의 _cache에서 피델리티 메시지를 추출해 세션 재오픈 없이 처리."""
    from agents import is_claude_token_exhausted
    if is_claude_token_exhausted():
        return
    try:
        # telegram_fetcher 캐시에서 피델리티 그룹 메시지 추출
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from telegram_fetcher import _cache, _get_groups, _normalize_group

        fidelity_key = _normalize_group(str(FIDELITY_GROUP_ID))
        cached = _cache.get(fidelity_key, {}).get("messages", [])
        if not cached:
            return

        by_member: dict[str, list[str]] = {}
        for m in cached:
            sender = m.get("sender", "알수없음")
            text = m.get("text", "")
            if sender and text and len(text) >= 5:
                by_member.setdefault(sender, []).append(text[:300])

        if by_member:
            _process_members(by_member)
    except Exception as e:
        logger.debug(f"캐시 기반 멤버 분석 오류: {e}")


def register_bot_name(member_name: str, bot_name: str):
    """멤버→봇 이름 매핑 등록."""
    with _conn() as con:
        con.execute("UPDATE member_profiles SET bot_name=? WHERE name=?", (bot_name, member_name))


def get_all_profiles() -> list[dict]:
    """저장된 모든 멤버 프로필 반환."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM member_profiles ORDER BY messages_collected DESC"
        ).fetchall()
        return [dict(r) for r in rows]
