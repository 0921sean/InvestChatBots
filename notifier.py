import os
import json
import time
import smtplib
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 쿨다운: {알림 키 → 마지막 발송 시각}
_cooldown: dict[str, float] = {}
COOLDOWN_SECONDS = 4 * 3600  # 4시간

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "investchat-sean-alerts")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# 진짜 크레딧/결제 문제만 잡음 (TPM/RPM 한도 초과는 제외)
CREDIT_KEYWORDS = ("credit balance", "billing", "insufficient_quota",
                   "payment", "your account", "upgrade your plan",
                   "ResourceExhausted")

# 속도 제한 (크레딧과 무관 — 별도 메시지)
RATE_LIMIT_KEYWORDS = ("rate_limit_exceeded", "tokens per min", "too large for",
                       "requests per min", "quota exceeded")

def is_credit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    # rate limit이면 크레딧 문제로 보지 않음
    if any(k.lower() in msg for k in RATE_LIMIT_KEYWORDS):
        return False
    return any(k.lower() in msg for k in CREDIT_KEYWORDS)

def is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k.lower() in msg for k in RATE_LIMIT_KEYWORDS)

def notify(title: str, body: str, priority: str = "default", cooldown: int = COOLDOWN_SECONDS):
    """ntfy.sh 푸시 + Gmail. cooldown(초) 내 같은 제목 알림은 1회만 발송."""
    key = title.strip()
    now = time.time()
    if cooldown > 0 and now - _cooldown.get(key, 0) < cooldown:
        return  # 쿨다운 중 — 무시
    _cooldown[key] = now
    _ntfy(title, body, priority)
    _email(title, body)

_PRIORITY_MAP = {"max": 5, "urgent": 5, "high": 4, "default": 3, "low": 2, "min": 1}

def _ntfy(title: str, body: str, priority: str):
    try:
        prio_int = _PRIORITY_MAP.get(priority, 3)
        payload = json.dumps({
            "topic": NTFY_TOPIC,
            "title": title,
            "message": body,
            "priority": prio_int,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        import logging
        logging.getLogger("investchat").warning(f"ntfy 전송 실패: {e}")

def _email(title: str, body: str):
    if not NOTIFY_EMAIL or not SMTP_PASSWORD:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[InvestChat] {title}"
        msg["From"] = NOTIFY_EMAIL
        msg["To"] = NOTIFY_EMAIL
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(NOTIFY_EMAIL, SMTP_PASSWORD)
            s.send_message(msg)
    except Exception:
        pass
