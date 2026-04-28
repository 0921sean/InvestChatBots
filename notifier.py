import os
import json
import smtplib
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "investchat-sean-alerts")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

CREDIT_KEYWORDS = ("credit", "quota", "billing", "insufficient", "exhausted",
                   "exceeded", "ResourceExhausted", "BadRequestError",
                   "rate_limit", "RateLimitError", "too large", "tokens per")

def is_credit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k.lower() in msg for k in CREDIT_KEYWORDS)

def notify(title: str, body: str, priority: str = "default"):
    """ntfy.sh 푸시 + Gmail 이메일(설정된 경우) 동시 발송."""
    _ntfy(title, body, priority)
    _email(title, body)

def _ntfy(title: str, body: str, priority: str):
    try:
        data = json.dumps({
            "topic": NTFY_TOPIC,
            "title": title,
            "message": body,
            "priority": priority,  # "max", "high", "default", "low", "min"
            "tags": ["investchat"],
        }).encode()
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

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
