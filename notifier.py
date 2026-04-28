import os
import json
import smtplib
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

def notify(title: str, body: str, priority: str = "default"):
    """ntfy.sh 푸시 + Gmail 이메일(설정된 경우) 동시 발송."""
    _ntfy(title, body, priority)
    _email(title, body)

def _ntfy(title: str, body: str, priority: str):
    try:
        # JSON body 방식 — topic별 URL에 POST
        payload = json.dumps({
            "topic": NTFY_TOPIC,
            "title": title,
            "message": body,
            "priority": priority,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title": urllib.parse.quote(title),
                "Priority": priority,
                "Content-Type": "text/plain; charset=utf-8",
            },
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
