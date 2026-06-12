"""
봇 매수/매도 토론을 PNG 이미지로 렌더 (헤드리스 브라우저 없이 Pillow만 사용).
라운드별로 한 번 생성하면 static/debates/round_{id}.png 로 캐시된다.
"""
import os
import re
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]
_FONT_PATH = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)

# 색 (프론트 BOT_COLORS와 동기)
_HEX = {
    "드가자": "ef4444", "INTJ": "3b82f6", "퀀트중독자": "8b5cf6", "빅픽처": "f59e0b",
    "차트천재": "10b981", "기본농부": "06b6d4", "실적왕": "22d3ee",
    "추세질주": "fb7185", "테마사냥꾼": "f472b6", "세력추적": "c084fc",
    "바닥픽": "38bdf8", "칼손절": "94a3b8",
}
BG = (13, 17, 23); SYSBG = (28, 33, 40)
TXT = (201, 209, 217); SUB = (139, 148, 158); MUTE = (110, 118, 129); WHITE = (230, 237, 243)

# 이모지·색 글리프 제거 (Apple 폰트는 컬러 이모지 렌더 못 함 → 박스 방지)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿️]"
)


def _font(size):
    if not _FONT_PATH:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(_FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _rgb(name):
    h = _HEX.get(name, "8b949e")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _clean(s):
    return _EMOJI.sub("", s or "").replace("\r", "")


def _wrap(draw, text, font, maxw):
    lines = []
    for raw in text.split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        cur = ""
        for ch in raw:
            if draw.textlength(cur + ch, font=font) <= maxw:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def render_debate_png(out_path, title, subtitle, messages):
    W, PAD = 680, 24
    maxw = W - 2 * PAD
    f_title, f_sub, f_name, f_body, f_sys = _font(21), _font(12), _font(15), _font(14), _font(13)
    LH_BODY, LH_SYS = 20, 18

    dummy = ImageDraw.Draw(Image.new("RGB", (W, 4)))
    blocks = []   # (kind, name|None, lines, height)
    for m in messages:
        name = m["agent_name"]
        content = _clean((m.get("content") or "").strip())
        if not content:
            continue
        if name in ("System", "User"):
            lines = _wrap(dummy, content, f_sys, maxw - 16)
            h = 10 + len(lines) * LH_SYS + 10
            blocks.append(("sys", None, lines, h))
        else:
            lines = _wrap(dummy, content, f_body, maxw)
            h = 24 + len(lines) * LH_BODY + 6
            blocks.append(("bot", name, lines, h))

    head_h = 18 + 28 + 24
    total = head_h + sum(b[3] + 8 for b in blocks) + 36

    img = Image.new("RGB", (W, total), BG)
    d = ImageDraw.Draw(img)
    y = 18
    d.text((PAD, y), _clean(title), font=f_title, fill=WHITE); y += 30
    d.text((PAD, y), _clean(subtitle), font=f_sub, fill=SUB); y += 24

    for kind, name, lines, h in blocks:
        if kind == "sys":
            d.rounded_rectangle([PAD, y, W - PAD, y + h - 8], radius=6, fill=SYSBG)
            ty = y + 8
            for ln in lines:
                d.text((PAD + 8, ty), ln, font=f_sys, fill=SUB); ty += LH_SYS
        else:
            d.text((PAD, y), name, font=f_name, fill=_rgb(name)); ty = y + 24
            for ln in lines:
                d.text((PAD, ty), ln, font=f_body, fill=TXT); ty += LH_BODY
        y += h + 8

    d.text((PAD, total - 26), "InvestChatBots · AI 봇 투자 토론", font=f_sub, fill=MUTE)
    img.save(out_path, "PNG")
    return out_path
