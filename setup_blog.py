"""
블로그 최초 전체 크롤링 — 1회만 실행

실행: python setup_blog.py
"""
import os, time
from dotenv import load_dotenv
load_dotenv()

from db import init_db, get_blog_post_count
from blog_fetcher import crawl_blog_full, get_blog_ids

init_db()
blog_ids = get_blog_ids()

if not blog_ids:
    print("❌ .env에 BLOG_URLS가 설정되지 않았습니다.")
    print("   예: BLOG_URLS=https://blog.naver.com/butterdaddy,https://blog.naver.com/ranto28")
    exit(1)

print(f"📖 블로그 {len(blog_ids)}개 전체 크롤링 시작")
print(f"   최근 6개월 본문 수집 (최대 300개/블로그), 이전 글은 제목만")
print()

for blog_id in blog_ids:
    before = get_blog_post_count(blog_id)
    print(f"▶ {blog_id} 크롤링 중... (기존 {before}개)")
    start = time.time()
    crawl_blog_full(blog_id)
    after = get_blog_post_count(blog_id)
    elapsed = time.time() - start
    print(f"  완료: {after}개 저장 ({after - before}개 신규) — {elapsed:.0f}초")
    print()

print("🎉 완료. 이후 서버 실행 시 RSS로 새 글만 자동 업데이트됩니다.")
