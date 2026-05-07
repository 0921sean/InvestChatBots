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
