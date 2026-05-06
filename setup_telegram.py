"""
텔레그램 최초 인증 스크립트 — 1회만 실행하면 됩니다.
실행: python setup_telegram.py

전화번호 + 인증코드 입력하면 telegram_session.session 파일이 생성되고
이후 서버 실행 시 자동으로 인증됩니다.
"""
import os
from dotenv import load_dotenv

load_dotenv()

api_id   = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
group    = os.getenv("TELEGRAM_GROUP", "")

if not api_id or not api_hash:
    print("❌ .env 파일에 TELEGRAM_API_ID와 TELEGRAM_API_HASH를 먼저 설정해주세요.")
    print("   발급: https://my.telegram.org → API development tools")
    exit(1)

from telethon.sync import TelegramClient

print("📱 텔레그램 로그인 시작...")
print("   (my.telegram.org에서 발급한 API 키를 .env에 설정했는지 확인하세요)\n")

session_path = os.getenv("TELEGRAM_SESSION", "telegram_session")

with TelegramClient(session_path, int(api_id), api_hash) as client:
    client.start()  # 전화번호 + 인증코드 입력 프롬프트 자동 표시
    me = client.get_me()
    print(f"\n✅ 로그인 성공: {me.first_name} (@{me.username})")
    print(f"   세션 저장됨: {session_path}.session\n")

    if group:
        print(f"📋 그룹 테스트: {group}")
        try:
            msgs = list(client.iter_messages(group, limit=5))
            print(f"   최근 메시지 {len(msgs)}개 수신 확인:")
            for m in reversed(msgs):
                if m.text:
                    print(f"   - {m.text[:80]}")
        except Exception as e:
            print(f"   ⚠️ 그룹 접근 실패: {e}")
            print("   TELEGRAM_GROUP 값을 확인해주세요. (예: t.me/xxx 또는 @groupname)")
    else:
        print("ℹ️  TELEGRAM_GROUP 미설정 — .env에 추가하면 그룹 메시지를 수집할 수 있습니다.")

print("\n🎉 설정 완료. 이제 서버를 시작하면 텔레그램 컨텍스트가 자동으로 AI 봇에 입력됩니다.")
