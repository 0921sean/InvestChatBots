"""
네이버 블로그 수집기
- 전체 글 목록: PostTitleListAsync API (페이지네이션)
- 본문 내용: 모바일 URL 스크래핑
- 최신 글: RSS
- DB 저장 후 봇 컨텍스트로 주입
"""
import os
import re
import time
import logging
import urllib.parse
import urllib.request
import json
from datetime import datetime, timedelta, timezone, date

logger = logging.getLogger("investchat.blog")

BLOG_URLS_ENV = "BLOG_URLS"          # 쉼표 구분 블로그 URL 목록
CONTENT_MONTHS = 6                    # 본문 가져올 기간 (최근 N개월)
MAX_CONTENT_POSTS = 300               # 본문 최대 수집 수 (첫 크롤링)
CRAWL_DELAY = 0.5                     # 요청 간 딜레이 (초)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def _extract_blog_id(url: str) -> str:
    """URL에서 네이버 블로그 ID 추출."""
    m = re.search(r'blog\.naver\.com/([^/?#]+)', url)
    return m.group(1) if m else url.strip("/").split("/")[-1]


def get_blog_ids() -> list[str]:
    raw = os.getenv(BLOG_URLS_ENV, "")
    return [_extract_blog_id(u.strip()) for u in raw.split(",") if u.strip()]


def _fetch_post_list(blog_id: str, page: int = 1, count: int = 30) -> dict:
    """네이버 블로그 글 목록 API."""
    url = (f"https://blog.naver.com/PostTitleListAsync.naver"
           f"?blogId={blog_id}&currentPage={page}&countPerPage={count}&categoryNo=0")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"포스트 목록 오류 {blog_id} p{page}: {e}")
        return {}


def _fetch_post_content(blog_id: str, log_no: str) -> str:
    """모바일 URL로 본문 텍스트 추출."""
    url = f"https://m.blog.naver.com/{blog_id}/{log_no}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # se-main-container 또는 postViewArea 에서 텍스트 추출
        for pattern in [
            r'<div[^>]*class="[^"]*se-main-container[^"]*"[^>]*>(.*?)</div>\s*</div>',
            r'<div[^>]*id="postViewArea"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*post_ct[^"]*"[^>]*>(.*?)</div>',
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                raw = m.group(1)
                text = re.sub(r'<[^>]+>', ' ', raw)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 100:
                    return text[:2000]
        # 폴백: 전체 텍스트에서 태그 제거
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[500:2500] if len(text) > 500 else text
    except Exception as e:
        logger.debug(f"본문 오류 {blog_id}/{log_no}: {e}")
        return ""


def _parse_naver_date(date_str: str) -> str | None:
    """'2026. 5. 7.' 또는 '6시간 전' 형식 → 'YYYY-MM-DD'."""
    date_str = date_str.strip()
    m = re.match(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?', date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if '분 전' in date_str or '시간 전' in date_str or '방금' in date_str:
        return date.today().isoformat()
    if '일 전' in date_str:
        n = int(re.search(r'\d+', date_str).group())
        return (date.today() - timedelta(days=n)).isoformat()
    return date.today().isoformat()


def crawl_blog_full(blog_id: str):
    """블로그 전체 글 수집 — 최초 1회 실행용."""
    from db import save_blog_post, blog_post_exists

    logger.info(f"[{blog_id}] 전체 크롤링 시작...")
    cutoff_date = (date.today() - timedelta(days=CONTENT_MONTHS * 30)).isoformat()

    page, content_count = 1, 0
    while True:
        data = _fetch_post_list(blog_id, page=page, count=30)
        posts = data.get("postList", [])
        if not posts:
            break

        total = int(data.get("totalCount", 0))
        logger.info(f"  [{blog_id}] p{page} — {len(posts)}개 (총 {total}개)")

        for post in posts:
            log_no = post.get("logNo", "")
            title = urllib.parse.unquote(post.get("title", "")).replace("+", " ")
            post_date = _parse_naver_date(post.get("addDate", "")) or date.today().isoformat()

            if blog_post_exists(blog_id, log_no):
                continue

            # 최근 N개월 + 최대 MAX_CONTENT_POSTS개는 본문 수집
            content = ""
            if post_date >= cutoff_date and content_count < MAX_CONTENT_POSTS:
                content = _fetch_post_content(blog_id, log_no)
                content_count += 1
                time.sleep(CRAWL_DELAY)

            save_blog_post(blog_id, log_no, title, post_date, content)

        page += 1
        if page * 30 > total + 30:
            break
        time.sleep(CRAWL_DELAY)

    logger.info(f"[{blog_id}] 크롤링 완료 — 본문 {content_count}개 수집")


def fetch_blog_rss(blog_id: str) -> list[dict]:
    """RSS로 최신 글 수집 (주기적 업데이트용)."""
    import feedparser
    from db import save_blog_post, blog_post_exists

    url = f"https://rss.blog.naver.com/{blog_id}"
    try:
        feed = feedparser.parse(url)
        new_count = 0
        for entry in feed.entries:
            link = getattr(entry, "link", "")
            log_no_m = re.search(r'/(\d+)', link)
            if not log_no_m:
                continue
            log_no = log_no_m.group(1)
            if blog_post_exists(blog_id, log_no):
                continue
            title = getattr(entry, "title", "")
            content = getattr(entry, "summary", "")[:2000]
            pub = getattr(entry, "published_parsed", None)
            post_date = (datetime(*pub[:3]).strftime("%Y-%m-%d") if pub
                         else date.today().isoformat())
            save_blog_post(blog_id, log_no, title, post_date, content)
            new_count += 1
        if new_count:
            logger.info(f"[{blog_id}] RSS 신규 {new_count}개")
    except Exception as e:
        logger.debug(f"RSS {blog_id}: {e}")


def get_recent_blog_context(days: int = 30, max_posts: int = 20) -> str:
    """최근 N일 블로그 글을 봇 프롬프트용 문자열로 반환."""
    from db import get_recent_blog_posts
    blog_ids = get_blog_ids()
    if not blog_ids:
        return ""

    since = (date.today() - timedelta(days=days)).isoformat()
    posts = get_recent_blog_posts(since=since, limit=max_posts)
    if not posts:
        return ""

    lines = [f"=== 블로그 최근 글 ({len(posts)}개) ==="]
    for p in posts:
        content_preview = p["content"][:200].replace("\n", " ") if p["content"] else ""
        lines.append(f"[{p['post_date']}][{p['blog_id']}] {p['title']}")
        if content_preview:
            lines.append(f"  {content_preview}")
    return "\n".join(lines)


def refresh_all_blogs():
    """RSS로 모든 블로그 업데이트 (주기적 호출용)."""
    for blog_id in get_blog_ids():
        try:
            fetch_blog_rss(blog_id)
        except Exception as e:
            logger.warning(f"블로그 업데이트 실패 {blog_id}: {e}")


# ── 신호 추출 ─────────────────────────────────────────────
def extract_signals_for_stock(stock_name: str, stock_code: str,
                               current_price: float) -> list[dict]:
    """
    stock_name을 언급한 블로그 글들에서 (언급가격, 판단) 추출.
    추출 결과는 DB에 캐시 → 이미 추출된 글은 재처리 안 함.
    """
    from db import (get_posts_mentioning, save_blog_signal,
                    signal_already_extracted, get_matching_signals)
    import os

    posts = get_posts_mentioning(stock_name, limit=30)
    if not posts:
        return get_matching_signals(stock_name, current_price)

    # AI 추출 — Anthropic API 직접 호출 (저렴한 Haiku 사용)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return get_matching_signals(stock_name, current_price)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    new_extractions = 0
    for post in posts:
        if signal_already_extracted(post["blog_id"], post["log_no"], stock_name):
            continue
        if new_extractions >= 10:  # 한 번에 최대 10개 처리
            break

        content = post.get("content", "")[:1500]
        if not content:
            continue

        prompt = f"""다음 블로그 글에서 '{stock_name}' 관련 투자 정보를 추출하세요.

블로그 글 (날짜: {post['post_date']}):
{content}

아래 JSON 형식으로만 답하세요. 정보가 없으면 null:
{{
  "mentioned_price": <언급된 가격(숫자, 원 단위) 또는 null>,
  "judgment": <"매수"|"관망"|"매도"|"주목"|"중립" 또는 null>,
  "excerpt": <핵심 문장 1개 (50자 이내) 또는 null>
}}"""

        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
                timeout=15.0,
            )
            text = resp.content[0].text.strip()
            # JSON 파싱
            import json, re
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if not m:
                continue
            data = json.loads(m.group())
            price = data.get("mentioned_price")
            judgment = data.get("judgment")
            excerpt = data.get("excerpt") or ""

            if price and judgment:
                save_blog_signal(
                    post["blog_id"], post["log_no"], post["post_date"],
                    stock_name, stock_code, float(price), judgment,
                    f"{post['title']} | {excerpt}"
                )
                new_extractions += 1
        except Exception as e:
            logger.debug(f"신호 추출 오류 {post['log_no']}: {e}")
        time.sleep(0.3)

    return get_matching_signals(stock_name, current_price)


def format_historical_signals(signals: list[dict], stock_name: str,
                               current_price: float) -> str:
    """과거 유사 구간 신호를 프롬프트용 텍스트로 포맷."""
    if not signals:
        return ""

    lines = [f"=== {stock_name} 유사 구간 블로거 과거 판단 (현재가 ₩{current_price:,.0f} 기준 ±15%) ==="]
    judgment_emoji = {"매수": "🟢", "관망": "🟡", "주목": "🔵", "매도": "🔴", "중립": "⚪"}
    for s in signals:
        emoji = judgment_emoji.get(s["judgment"], "•")
        price_str = f"₩{s['mentioned_price']:,.0f}" if s["mentioned_price"] else "?"
        lines.append(
            f"{emoji} [{s['post_date']}][{s['blog_id']}] {s['judgment']} "
            f"(당시가 {price_str})\n  {s['excerpt']}"
        )
    return "\n".join(lines)
