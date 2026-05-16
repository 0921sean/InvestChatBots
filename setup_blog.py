"""
블로그 최초 전체 크롤링 — 1회만 실행

- 기간 제한 없이 모든 글 본문 수집
- 요청 간 2.5~5초 랜덤 딜레이, 30개마다 20~40초 긴 휴식
- 중단 후 재실행하면 이미 수집된 글은 건너뜀 (이어서 진행)

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
    print("   예: BLOG_URLS=https://blog.naver.com/blog1")
    exit(1)

print(f"📖 블로그 {len(blog_ids)}개 전체 크롤링 시작")
print(f"   전체 본문 수집 (기간 제한 없음)")
print(f"   요청 딜레이: 2.5~5초 / 30개마다 20~40초 휴식")
print(f"   중단 시 재실행하면 이어서 진행됩니다.\n")

for blog_id in blog_ids:
    before = get_blog_post_count(blog_id)
    print(f"▶ {blog_id} 크롤링 중... (기존 {before}개)")
    start = time.time()
    crawl_blog_full(blog_id, fetch_all_content=True)
    after = get_blog_post_count(blog_id)
    elapsed = time.time() - start
    print(f"  완료: {after}개 저장 ({after - before}개 신규) — {elapsed/60:.1f}분\n")

print("🎉 완료. 이후 서버 실행 시 RSS로 새 글만 자동 업데이트됩니다.")
