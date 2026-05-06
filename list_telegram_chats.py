"""
텔레그램 참여 중인 채팅 목록 출력
비공개 방 ID 확인용 — 1회 실행 후 .env에 ID 복사

실행: python list_telegram_chats.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

from telethon.sync import TelegramClient
from telethon.tl.types import (
    Chat, Channel, User,
    ChatForbidden, ChannelForbidden,
)

api_id   = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
session  = os.getenv("TELEGRAM_SESSION", "telegram_session")

if not api_id or not api_hash:
    print("❌ TELEGRAM_API_ID / TELEGRAM_API_HASH 미설정")
    exit(1)

with TelegramClient(session, int(api_id), api_hash) as client:
    print(f"\n{'─'*65}")
    print(f"{'종류':<8} {'ID':<18} {'이름'}")
    print(f"{'─'*65}")

    for dialog in client.iter_dialogs():
        entity = dialog.entity

        if isinstance(entity, (ChatForbidden, ChannelForbidden)):
            continue

        if isinstance(entity, User):
            kind = "DM"
        elif isinstance(entity, Chat):
            kind = "그룹"
        elif isinstance(entity, Channel):
            kind = "채널" if entity.broadcast else "슈퍼그룹"
        else:
            continue

        chat_id = entity.id

        # 채널/슈퍼그룹은 앞에 -100 붙여야 Telethon에서 인식
        if isinstance(entity, Channel):
            full_id = int(f"-100{entity.id}")
        elif isinstance(entity, Chat):
            full_id = -entity.id
        else:
            full_id = entity.id

        name = dialog.name or "(이름 없음)"
        username = getattr(entity, "username", None)
        display = f"@{username}" if username else f"ID: {full_id}"

        print(f"{kind:<8} {str(full_id):<18} {name}  [{display}]")

    print(f"{'─'*65}")
    print("\n💡 비공개 방은 ID(숫자)를 .env에 추가하세요:")
    print("   TELEGRAM_GROUPS=t.me/공개방,-1001234567890,-1009876543210")
