"""
봇 매수/매도 토론을 '실제 채팅 화면처럼' PNG로 렌더 (Pillow, 헤드리스 브라우저 불필요).
아바타 + 말풍선 + [결정] 색상(매수 초록/매도 빨강/관망 노랑)을 라이브 채팅과 동일하게 재현.
길면 여러 장으로 분할. static/debates/round_{id}_{n}.png 로 캐시.
"""
import os
import re
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]
_FONT_PATH = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)

_HEX = {
    "드가자": "ef4444", "INTJ": "3b82f6", "퀀트중독자": "8b5cf6", "빅픽처": "f59e0b",
    "역추세봇": "f59e0b",
    "차트천재": "10b981", "기본농부": "06b6d4", "실적왕": "22d3ee",
    "추세질주": "fb7185", "테마사냥꾼": "f472b6", "세력추적": "c084fc",
    "바닥픽": "38bdf8", "칼손절": "94a3b8",
}
_ROLE = {
    "드가자": "낙관·매수파", "INTJ": "리스크 관리", "퀀트중독자": "수치·기준선", "빅픽처": "매크로",
    "역추세봇": "평균회귀 역추세", "차트천재": "MACD·RSI", "기본농부": "펀더·장투", "실적왕": "성장주(GARP)·PEG",
    "추세질주": "모멘텀", "테마사냥꾼": "테마·뉴스", "세력추적": "수급·이벤트",
    "바닥픽": "역추세", "칼손절": "리스크컷",
}

BG = (13, 17, 23)            # 페이지
BUBBLE = (28, 33, 40)        # 말풍선 #1c2128
BORDER = (48, 54, 61)        # #30363d
SYSBG = (28, 33, 40)
TXT = (201, 209, 217)        # 본문
GRAY = (110, 118, 129)       # 메타·시스템 #6e7681
SYSTXT = (139, 148, 158)
WHITE = (230, 237, 243)
MUTE = (96, 103, 112)
DEC = {"매수": (63, 185, 80), "매도": (248, 81, 73), "관망": (210, 153, 34), "홀드": (210, 153, 34)}

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿️🗳]")
_DECRE = re.compile(r'(\[결정\]\s*(매수|매도|관망|홀드))')

W, PAD = 760, 22
AV, GAP = 34, 10
LH = 20

_F = {
    "title": (ImageFont.truetype(_FONT_PATH, 22) if _FONT_PATH else ImageFont.load_default()),
    "sub": (ImageFont.truetype(_FONT_PATH, 12) if _FONT_PATH else ImageFont.load_default()),
    "name": (ImageFont.truetype(_FONT_PATH, 14) if _FONT_PATH else ImageFont.load_default()),
    "meta": (ImageFont.truetype(_FONT_PATH, 11) if _FONT_PATH else ImageFont.load_default()),
    "body": (ImageFont.truetype(_FONT_PATH, 14) if _FONT_PATH else ImageFont.load_default()),
    "av": (ImageFont.truetype(_FONT_PATH, 15) if _FONT_PATH else ImageFont.load_default()),
}
_DUMMY = ImageDraw.Draw(Image.new("RGB", (W, 4)))


def _rgb(name):
    h = _HEX.get(name, "8b949e")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _clean(s):
    return _EMOJI.sub("", s or "").replace("\r", "").strip()


def _bold(d, xy, text, font, fill):
    d.text(xy, text, font=font, fill=fill)
    d.text((xy[0] + 1, xy[1]), text, font=font, fill=fill)  # faux bold


def _wrap(text, font, maxw):
    lines = []
    for raw in text.split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        cur = ""
        for ch in raw:
            if _DUMMY.textlength(cur + ch, font=font) <= maxw:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def _hhmm(ts):
    # "YYYY-MM-DD HH:MM:SS" (UTC) → KST HH:MM
    try:
        hh, mm = ts[11:13], ts[14:16]
        h = (int(hh) + 9) % 24
        return f"{h:02d}:{mm}"
    except Exception:
        return ""


def _draw_line(d, x, y, line, font, base):
    m = _DECRE.search(line)
    if not m:
        d.text((x, y), line, font=font, fill=base)
        return
    pre, dec, post = line[:m.start()], m.group(1), line[m.end():]
    cx = x
    if pre:
        d.text((cx, y), pre, font=font, fill=base); cx += d.textlength(pre, font=font)
    col = DEC.get(m.group(2), base)
    _bold(d, (cx, y), dec, font, col); cx += d.textlength(dec, font=font)
    if post:
        d.text((cx + 1, y), post, font=font, fill=base)


def _build_blocks(messages):
    x0 = PAD + AV + GAP
    max_bubble = W - x0 - PAD
    blocks = []
    for m in messages:
        name = m["agent_name"]
        content = _clean(m.get("content") or "")
        if not content:
            continue
        if name in ("System", "User"):
            lines = _wrap(content, _F["sys"] if "sys" in _F else _F["body"], W - 2 * PAD - 28)
            longest = max((_DUMMY.textlength(ln, font=_F["body"]) for ln in lines), default=0)
            box_w = int(min(W - 2 * PAD, longest + 28))
            box_h = len(lines) * 18 + 14
            blocks.append({"t": "sys", "lines": lines, "box_w": box_w, "box_h": box_h,
                           "height": box_h + 12})
        else:
            lines = _wrap(content, _F["body"], max_bubble - 24)
            longest = max((_DUMMY.textlength(ln, font=_F["body"]) for ln in lines), default=0)
            bubble_w = int(min(max_bubble, longest + 24))
            bubble_h = len(lines) * LH + 14
            blocks.append({"t": "bot", "name": name, "time": _hhmm(m.get("timestamp", "")),
                           "lines": lines, "bw": bubble_w, "bh": bubble_h,
                           "height": max(AV, 22 + bubble_h) + 10})
    return blocks


def _split_blocks(blocks, pages):
    n = len(blocks)
    if pages <= 1 or n <= 1:
        return [blocks]
    pages = min(pages, n)
    total = sum(b["height"] for b in blocks)
    target = total / pages
    groups, cur, cur_h = [], [], 0
    for i, b in enumerate(blocks):
        cur.append(b); cur_h += b["height"]
        blocks_left = n - (i + 1)
        pages_left = pages - len(groups) - 1
        if pages_left > 0 and (cur_h >= target or blocks_left <= pages_left):
            groups.append(cur); cur, cur_h = [], 0
    if cur:
        groups.append(cur)
    return groups


def _render_page(out_path, title, subtitle, blocks):
    head_h = 18 + 30 + 22
    total = head_h + sum(b["height"] for b in blocks) + 34
    img = Image.new("RGB", (W, total), BG)
    d = ImageDraw.Draw(img)
    y = 18
    _bold(d, (PAD, y), _clean(title), _F["title"], WHITE); y += 30
    d.text((PAD, y), _clean(subtitle), font=_F["sub"], fill=SYSTXT); y += 22
    x0 = PAD + AV + GAP
    for b in blocks:
        if b["t"] == "sys":
            bw, bh = b["box_w"], b["box_h"]
            bx = (W - bw) // 2
            d.rounded_rectangle([bx, y, bx + bw, y + bh], radius=9, fill=SYSBG, outline=BORDER, width=1)
            ty = y + 8
            for ln in b["lines"]:
                d.text((bx + 14, ty), ln, font=_F["sys"] if "sys" in _F else _F["body"], fill=SYSTXT)
                ty += 18
            y += b["height"]
        else:
            name = b["name"]; col = _rgb(name)
            # 아바타 (둥근 사각, 색 틴트 배경 + 색 테두리 + 첫 글자)
            d.rounded_rectangle([PAD, y, PAD + AV, y + AV], radius=7,
                                fill=_mix(BG, col, 0.16), outline=_mix(BG, col, 0.55), width=2)
            ch = name[0]
            cw = d.textlength(ch, font=_F["av"])
            _bold(d, (PAD + (AV - cw) / 2 - 0.5, y + 7), ch, _F["av"], col)
            # 메타 (이름 + 역할 + 시간)
            _bold(d, (x0, y + 1), name, _F["name"], col)
            nx = x0 + d.textlength(name, font=_F["name"]) + 7
            role = _ROLE.get(name, "")
            if role:
                d.text((nx, y + 4), role, font=_F["meta"], fill=GRAY)
                nx += d.textlength(role, font=_F["meta"]) + 7
            if b["time"]:
                d.text((nx, y + 4), b["time"], font=_F["meta"], fill=MUTE)
            # 말풍선
            by = y + 22
            d.rounded_rectangle([x0, by, x0 + b["bw"], by + b["bh"]], radius=8,
                                fill=BUBBLE, outline=BORDER, width=1)
            ty = by + 8
            for ln in b["lines"]:
                _draw_line(d, x0 + 12, ty, ln, _F["body"], TXT)
                ty += LH
            y += b["height"]
    d.text((PAD, total - 24), "InvestChatBots · AI 봇 투자 토론", font=_F["sub"], fill=MUTE)
    img.save(out_path, "PNG")
    return out_path


# sys 폰트 별도(작게)
_F["sys"] = (ImageFont.truetype(_FONT_PATH, 13) if _FONT_PATH else ImageFont.load_default())


def render_debate_pages(prefix, title, subtitle, messages, pages=2):
    blocks = _build_blocks(messages)
    groups = _split_blocks(blocks, pages)
    n = len(groups)
    out = []
    for i, grp in enumerate(groups, 1):
        sub = subtitle + (f"   ({i}/{n})" if n > 1 else "")
        out.append(_render_page(f"{prefix}_{i}.png", title, sub, grp))
    return out


def render_debate_png(out_path, title, subtitle, messages):
    return _render_page(out_path, title, subtitle, _build_blocks(messages))
