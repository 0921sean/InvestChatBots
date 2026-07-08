"""
'오늘의 봇 배틀' 세로 릴스(9:16) MP4 생성 — DB의 완결된 가상매매 서사 하나를 고정 템플릿에 꽂아 렌더.
헤드리스 브라우저·AI 생성영상 없이 Pillow 프레임 시퀀스 + imageio(ffmpeg)로 인코딩. 클립당 비용 0.
라이브 가격 API를 호출하지 않는다 — 청산 완료 포지션의 저장된 값만 사용(자체 완결).

  python reels_clip.py            # 최근 청산 포지션 중 가장 극적인 것 자동 선택
  python reels_clip.py 테슬라      # 특정 종목 지정

출력: Instagram/reels/<symbol>_<closed>.mp4  (+ 미리보기 poster PNG)
가드레일: 모든 프레임 하단에 '가상 포트폴리오 · 투자자문/권유 아님' 고정.
"""
import os
import re
import sqlite3
import sys

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import debate_image as di  # 색·역할 팔레트 재사용

DB = os.path.join(os.path.dirname(__file__), "investchat.db")
OUT_DIR = os.path.join(os.path.dirname(__file__), "Instagram", "reels")

W, H = 1080, 1920
BG = di.BG
FOOTER = "가상 포트폴리오 · 투자 자문/권유 아님 · AI 봇 의견"
FP = di._FONT_PATH


def _font(sz):
    return ImageFont.truetype(FP, sz) if FP else ImageFont.load_default()


F = {"kicker": _font(38), "title": _font(76), "big": _font(190), "sub": _font(46),
     "name": _font(44), "body": _font(40), "footer": _font(30), "cta": _font(66)}

RED = di.DEC["매도"]
GREEN = di.DEC["매수"]
WHITE = (235, 240, 247)
MUTE = (120, 128, 140)
BUBBLE = di.BUBBLE
BORDER = di.BORDER


def _bold(d, xy, text, font, fill):
    d.text(xy, text, font=font, fill=fill)
    d.text((xy[0] + 2, xy[1]), text, font=font, fill=fill)


def _tw(d, text, font):
    return d.textlength(text, font=font)


def _wrap(d, text, font, maxw):
    out = []
    for raw in text.split("\n"):
        cur = ""
        for ch in raw:
            if _tw(d, cur + ch, font) <= maxw:
                cur += ch
            else:
                out.append(cur); cur = ch
        out.append(cur)
    return out


def _clean(s):
    return di._clean(s)


# ── 블록 렌더러(세로 중앙 정렬) ──────────────────────────────
def _measure(d, block):
    t = block[0]
    if t == "kicker":
        return 38 + 26
    if t == "title":
        lines = _wrap(d, block[1], F["title"], W - 160)
        return len(lines) * 92 + 24
    if t == "sub":
        return 60
    if t == "big":
        return 210
    if t == "bubble":
        lines = _wrap(d, block[2], F["body"], W - 260)
        return (14 + 56 + len(lines) * 52 + 16) + 20
    if t == "line":
        return 62
    if t == "gap":
        return block[1]
    return 0


def _draw(d, y, block):
    t = block[0]
    if t == "kicker":
        txt = block[1]; w = _tw(d, txt, F["kicker"])
        _bold(d, ((W - w) / 2, y), txt, F["kicker"], block[2] if len(block) > 2 else GREEN)
        return y + 38 + 26
    if t == "title":
        for ln in _wrap(d, block[1], F["title"], W - 160):
            w = _tw(d, ln, F["title"]); _bold(d, ((W - w) / 2, y), ln, F["title"], WHITE); y += 92
        return y + 24
    if t == "sub":
        txt = block[1]; w = _tw(d, txt, F["sub"])
        d.text(((W - w) / 2, y), txt, font=F["sub"], fill=MUTE); return y + 60
    if t == "big":
        txt = block[1]; col = block[2]; w = _tw(d, txt, F["big"])
        _bold(d, ((W - w) / 2, y - 6), txt, F["big"], col); return y + 210
    if t == "bubble":
        name, text, col = block[1], block[2], block[3]
        lines = _wrap(d, text, F["body"], W - 260)
        bh = 14 + 56 + len(lines) * 52 + 16
        x0, x1 = 90, W - 90
        d.rounded_rectangle([x0, y, x1, y + bh], radius=22, fill=BUBBLE, outline=col, width=3)
        _bold(d, (x0 + 30, y + 14), name, F["name"], col)
        ty = y + 14 + 56
        for ln in lines:
            d.text((x0 + 30, ty), ln, font=F["body"], fill=WHITE); ty += 52
        return y + bh + 20
    if t == "line":
        name, tail, col = block[1], block[2], block[3]
        full = name + tail
        w = _tw(d, name, F["name"]) + _tw(d, tail, F["name"])
        x = (W - w) / 2
        _bold(d, (x, y), name, F["name"], col)
        d.text((x + _tw(d, name, F["name"]), y), tail, font=F["name"], fill=MUTE)
        return y + 62
    if t == "gap":
        return y + block[1]
    return y


def scene(blocks, accent=None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if accent:  # 상단 얇은 강조 바
        d.rectangle([0, 0, W, 10], fill=accent)
    total = sum(_measure(d, b) for b in blocks)
    y = (H - total) / 2 - 40
    for b in blocks:
        y = _draw(d, y, b)
    # 하단 면책 고지(모든 프레임 고정 — 가드레일)
    fw = _tw(d, FOOTER, F["footer"])
    d.text(((W - fw) / 2, H - 78), FOOTER, font=F["footer"], fill=MUTE)
    return img


# ── DB에서 극적인 청산 포지션 하나 뽑기 ─────────────────────
def pick_position(symbol=None):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM virtual_positions WHERE status='closed' "
        "AND reasoning IS NOT NULL AND exit_reasoning IS NOT NULL "
        "AND pnl_pct IS NOT NULL ORDER BY closed_at DESC LIMIT 25"
    ).fetchall()
    con.close()
    if symbol:
        rows = [r for r in rows if r["symbol"] == symbol] or rows
        if symbol:
            rows.sort(key=lambda r: r["symbol"] != symbol)
    else:
        rows = sorted(rows, key=lambda r: -abs(r["pnl_pct"] or 0))
    return rows[0] if rows else None


_BOT_NAMES = list(di._HEX.keys())
_VOTE_RE = re.compile(r"투표 결과:\s*(.+?)\s*(?:→\s*최종:\s*(\S+))?$", re.S)
_STOCK_RE = re.compile(r"([가-힣A-Za-z0-9·&]+)\s*\(([A-Z0-9.]{1,6})\)")
_PRICE_RE = re.compile(r"현재가:\s*([$₩][\d,.]+)\s*([▲▼]?\s*[\d.]+%)?")


def _round_votes(msgs):
    """🗳 System 메시지에서 (봇:결정) 목록과 최종 결정 파싱."""
    for m in msgs:
        if m["agent_name"] == "System" and "투표 결과" in (m["content"] or ""):
            body = m["content"].split("투표 결과:", 1)[1]
            final = None
            fm = re.search(r"최종:\s*(\S+)", body)
            if fm:
                final = fm.group(1).strip()
                body = body.split("→", 1)[0]
            votes = []
            for part in body.split("|"):
                vm = re.match(r"\s*([^:：]+)[:：]\s*(매수|매도|관망|홀드)", part)
                if vm:
                    votes.append((vm.group(1).strip(), vm.group(2)))
            return votes, final
    return [], None


def _round_stock(msgs):
    """System 데이터 메시지에서 종목명·티커·현재가 추출."""
    for m in msgs:
        c = m["content"] or ""
        if m["agent_name"] == "System" and ("데이터 수집" in c or "분석" in c):
            sm = _STOCK_RE.search(c)
            pm = _PRICE_RE.search(c)
            if sm:
                price = ""
                if pm:
                    price = pm.group(1) + ("  " + pm.group(2).replace(" ", "") if pm.group(2) else "")
                return sm.group(1), sm.group(2), price
    return None, None, ""


def _dialogue(msgs, limit=4):
    """봇 발언만 뽑아 (이름, 짧은 스니펫). 서로 이름 언급한(반박) 발언 우선."""
    bots = [m for m in msgs if m["agent_name"] in _BOT_NAMES and _clean(m["content"])]
    out = []
    for m in bots[:limit]:  # 원순서 = 논쟁 흐름(발단→반박) 보존
        out.append((m["agent_name"], _snippet(m["content"]), di._rgb(m["agent_name"])))
    return out


def _snippet(content, maxlen=56):
    t = _clean(content)
    # 앞머리 자기소개("차트천재요." "퀀트중독자 분석 들어간다") 가볍게 제거
    t = re.sub(r"^[가-힣A-Za-z]+(요\.?|입니다\.?|\s*분석\s*들어간다\.?)\s*", "", t)
    if len(t) <= maxlen:
        return t
    cut = t[:maxlen]
    sp = cut.rfind(" ")
    if sp > maxlen - 16:
        cut = cut[:sp]
    return cut.rstrip(" ,·") + "…"


def _load_round(round_id):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone()
    msgs = con.execute(
        "SELECT agent_name, content FROM messages WHERE round_id=? ORDER BY id", (round_id,)
    ).fetchall()
    con.close()
    return r, msgs


def pick_debate(market=None):
    """2단 우선순위: (1) 매매결정(최종 매수/매도) 난 최근 라운드 → (2) 없으면 가장 격한 토론.
    격함 = 상호 반박(다른 봇 이름 언급) + 표결 갈림 + 발언 수."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT DISTINCT m.round_id FROM messages m "
        "WHERE m.agent_name='System' AND m.content LIKE '%투표 결과%' "
        "ORDER BY m.round_id DESC LIMIT 60"
    ).fetchall()
    con.close()

    us = (market or "").upper() in ("US", "USD")
    cand = []
    for row in rows:
        rid = row["round_id"]
        r, msgs = _load_round(rid)
        topic = (r["topic"] if r else "") or ""
        if market:  # 시장 필터: 미장은 '미국' 접두, 국장은 그 외
            if us != topic.startswith("미국"):
                continue
        stock, ticker, price = _round_stock(msgs)
        if not stock:
            continue
        votes, final = _round_votes(msgs)
        if not votes:
            continue
        counts = {}
        for _, d in votes:
            counts[d] = counts.get(d, 0) + 1
        dissent = len(votes) - (max(counts.values()) if counts else 0)
        mentions = 0
        for m in msgs:
            if m["agent_name"] in _BOT_NAMES:
                c = _clean(m["content"])
                mentions += sum(1 for b in _BOT_NAMES if b != m["agent_name"] and b in c)
        heat = mentions + dissent * 2 + len([m for m in msgs if m["agent_name"] in _BOT_NAMES]) * 0.3
        cand.append({"rid": rid, "date": (r["started_at"] or "")[:10] if r else "",
                     "stock": stock, "ticker": ticker, "price": price,
                     "votes": votes, "final": final, "counts": counts, "heat": heat,
                     "traded": final in ("매수", "매도"), "msgs": msgs})

    if not cand:
        return None
    # 최신 사이클로 범위 한정: 가장 최근 날짜의 라운드만 (오래된 라운드 픽 방지)
    latest = max(c["date"] for c in cand)
    cand = [c for c in cand if c["date"] == latest]
    traded = [c for c in cand if c["traded"]]
    if traded:                                 # 1순위: 매매결정 → 가장 최근(rid 최대)
        return max(traded, key=lambda c: c["rid"])
    return max(cand, key=lambda c: c["heat"])  # 2순위: 가장 격한 토론


def _entry_reasons(reasoning):
    out = []
    for ln in (reasoning or "").split("\n"):
        m = re.match(r"\s*[•·\-]\s*([^:：]+)[:：]\s*(.+)", ln)
        if m:
            name = m.group(1).strip()
            t = _clean(m.group(2))
            text = (t[:42].rstrip(" ,·.") + "…") if len(t) > 44 else t
            out.append((name, text, di._rgb(name)))
    return out[:2]


def _sell_voters(exit_reasoning):
    names = []
    for part in (exit_reasoning or "").replace("\n", " ").split("/"):
        m = re.match(r"\s*([^:：]+)[:：]\s*매도", part)
        if m:
            names.append(m.group(1).strip())
    return names


def _price(v, market):
    cur = "$" if (market or "").upper() in ("US", "USD") else "₩"
    return f"{cur}{v:g}"


def build_scenes(p):
    sym = p["symbol"]
    pct = p["pnl_pct"] or 0
    won = pct >= 0
    col = GREEN if won else RED
    market = p["market"]
    reasons = _entry_reasons(p["reasoning"])
    sellers = _sell_voters(p["exit_reasoning"])
    days = _hold_days(p["opened_at"], p["closed_at"])
    pnl = int(p["pnl"] or 0)
    dir_word = p["direction"] or "매수"

    S = []
    # 1. 훅
    S.append((70, scene([
        ("kicker", "AI 봇 가상 계좌 · 실전 기록", GREEN),
        ("gap", 20),
        ("title", f"AI 봇들이\n{sym}를 샀다"),
        ("sub", f"{_price(p['entry_price'], market)}에 {dir_word} 진입"),
    ], accent=GREEN)))
    # 2. 진입 근거
    entry_blocks = [("kicker", "왜 샀나", GREEN), ("gap", 16)]
    for n, t, c in reasons:
        entry_blocks.append(("bubble", n, t, c))
    S.append((120, scene(entry_blocks, accent=GREEN)))
    # 3. 보유
    S.append((48, scene([
        ("title", f"그리고 {days}일 뒤…"),
    ])))
    # 4. 반전 — 손익
    S.append((85, scene([
        ("sub", f"{_price(p['entry_price'], market)}  →  {_price(p['exit_price'], market)}"),
        ("gap", 10),
        ("big", f"{pct:+.1f}%", col),
    ], accent=col)))
    # 5. 매도 투표
    vote_blocks = [("kicker", f"봇 {len(sellers)}명이 매도를 외쳤다", RED), ("gap", 20)]
    for n in sellers:
        vote_blocks.append(("line", n, "  매도", di._rgb(n)))
    S.append((115, scene(vote_blocks, accent=RED)))
    # 6. 결과
    S.append((85, scene([
        ("kicker", "청산 완료 (가상)", col),
        ("gap", 16),
        ("big", f"{pnl:+,}", col),
        ("sub", "가상 포트폴리오 손익 (원)"),
    ], accent=col)))
    # 7. CTA
    S.append((80, scene([
        ("title", "AI 7봇의\n가상 계좌를\n실시간 관전"),
        ("gap", 20),
        ("kicker", "InvestChatBots", GREEN),
    ], accent=GREEN)))
    return S


def _hold_days(a, b):
    try:
        from datetime import datetime
        fa = datetime.strptime(a[:10], "%Y-%m-%d")
        fb = datetime.strptime(b[:10], "%Y-%m-%d")
        return max(1, (fb - fa).days)
    except Exception:
        return 1


def render(symbol=None, fps=30, xfade=8):
    p = pick_position(symbol)
    if not p:
        print("청산 포지션 없음"); return None
    print(f"소재: {p['symbol']} {p['pnl_pct']:+.1f}% ({p['market']}) "
          f"{p['opened_at'][:10]}→{p['closed_at'][:10]}")
    os.makedirs(OUT_DIR, exist_ok=True)
    scenes = build_scenes(p)
    tag = f"{p['symbol']}_{p['closed_at'][:10]}"
    out = os.path.join(OUT_DIR, f"{tag}.mp4")

    prev = None
    writer = imageio.get_writer(out, fps=fps, codec="libx264", quality=8,
                                macro_block_size=8, ffmpeg_log_level="error")
    for hold, img in scenes:
        if prev is not None:
            for k in range(1, xfade + 1):
                blend = Image.blend(prev, img, k / xfade)
                writer.append_data(np.asarray(blend))
        for _ in range(hold):
            writer.append_data(np.asarray(img))
        prev = img
    writer.close()

    poster = os.path.join(OUT_DIR, f"{tag}_poster.png")
    scenes[3][1].save(poster)  # 손익 반전 씬을 커버로
    total = sum(h for h, _ in scenes) + xfade * (len(scenes) - 1)
    print(f"완료: {out}  ({total} frames ≈ {total / fps:.1f}s)")
    print(f"미리보기: {poster}")
    return out


DEC_COL = {"매수": GREEN, "매도": RED, "관망": di.DEC["관망"], "홀드": di.DEC["관망"]}


def build_debate_scenes(c):
    """'봇 토론 배틀' — 격한 논쟁을 대화 버블로 보여주고, 표결·최종 결정으로 마무리."""
    stock, ticker, price = c["stock"], c["ticker"], c["price"]
    final = c["final"] or "관망"
    fcol = DEC_COL.get(final, di.DEC["관망"])
    dialog = _dialogue(c["msgs"], limit=4)
    counts = c["counts"]

    S = []
    hook_k = "AI 봇 7명이 오늘 이 종목을 샀다" if c["traded"] and final == "매수" \
        else ("AI 봇 7명이 오늘 이 종목을 팔았다" if c["traded"] else "AI 봇 7명이 가장 뜨겁게 붙은 종목")
    # 1. 훅
    S.append((66, scene([
        ("kicker", hook_k, fcol),
        ("gap", 22),
        ("title", stock),
        ("sub", f"{ticker}   {price}".strip()),
    ], accent=fcol)))
    # 2~3. 대화(2버블씩 나눠 페이싱)
    for i in range(0, min(len(dialog), 4), 2):
        pair = dialog[i:i + 2]
        blocks = [("kicker", "봇들의 공방" if i == 0 else "반박", MUTE), ("gap", 16)]
        for n, t, col in pair:
            blocks.append(("bubble", n, t, col))
        S.append((118, scene(blocks, accent=None)))
    # 4. 표결 집계
    tally = [("kicker", "표결", fcol), ("gap", 18)]
    for dec in ("매수", "관망", "매도", "홀드"):
        if counts.get(dec):
            tally.append(("line", f"{dec} {counts[dec]}", "명", DEC_COL[dec]))
    S.append((90, scene(tally, accent=fcol)))
    # 5. 최종 결정
    last = [("kicker", "최종 결정", fcol), ("gap", 16), ("big", final, fcol)]
    if c["traded"]:
        last.append(("sub", f"가상 계좌에 {final} 반영"))
    else:
        last.append(("sub", "이번엔 관망 — 아무도 방아쇠를 안 당겼다"))
    S.append((92, scene(last, accent=fcol)))
    # 6. CTA
    S.append((78, scene([
        ("title", "AI 7봇의\n실시간 토론\n관전하기"),
        ("gap", 20),
        ("kicker", "InvestChatBots", GREEN),
    ], accent=GREEN)))
    return S


def _encode(scenes, out, fps=30, xfade=8):
    prev = None
    writer = imageio.get_writer(out, fps=fps, codec="libx264", quality=8,
                                macro_block_size=8, ffmpeg_log_level="error")
    for hold, img in scenes:
        if prev is not None:
            for k in range(1, xfade + 1):
                writer.append_data(np.asarray(Image.blend(prev, img, k / xfade)))
        for _ in range(hold):
            writer.append_data(np.asarray(img))
        prev = img
    writer.close()
    return sum(h for h, _ in scenes) + xfade * (len(scenes) - 1)


def render_debate(market=None, fps=30):
    c = pick_debate(market)
    if not c:
        print("토론 라운드 없음"); return None
    tier = "매매결정" if c["traded"] else "격한토론"
    print(f"[{tier}] 소재: {c['stock']} ({c['ticker']}) 최종={c['final']} "
          f"heat={c['heat']:.1f} round={c['rid']}")
    os.makedirs(OUT_DIR, exist_ok=True)
    scenes = build_debate_scenes(c)
    tag = f"debate_{c['ticker'] or c['stock']}_{c['rid']}"
    out = os.path.join(OUT_DIR, f"{tag}.mp4")
    total = _encode(scenes, out, fps)
    poster = os.path.join(OUT_DIR, f"{tag}_poster.png")
    scenes[0][1].save(poster)
    print(f"완료: {out}  ({total} frames ≈ {total / fps:.1f}s)")
    print(f"미리보기: {poster}")
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--position":
        render(a[1] if len(a) > 1 else None)   # 청산 포지션 P&L 아크(구버전)
    else:
        render_debate()                        # 기본: 2단 우선순위 토론 배틀
