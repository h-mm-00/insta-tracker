import os
import json
import re
from datetime import datetime, timezone, timedelta
from apify_client import ApifyClient
import firebase_admin
from firebase_admin import credentials, firestore

# 1. 수집할 계정 목록 (원하는 플랫폼을 여기에 계속 추가 가능)
ACCOUNTS = {
    "genie_music": "genie",
    "melon_music": "melon",
    "flo_official": "Flo",
}

# Firebase 초기화
firebase_cred_json = os.environ.get("FIREBASE_CREDENTIALS")
cred_dict = json.loads(firebase_cred_json)
cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Apify 초기화
apify_client = ApifyClient(os.environ.get("APIFY_API_TOKEN"))
KST = timezone(timedelta(hours=9))

def sync():
    posts_ref = db.collection("posts")

    for insta_handle, acct_code in ACCOUNTS.items():
        print(f"[{insta_handle}] 원본 데이터 수집 시작...")
        run_input = {
            "directUrls": [f"https://www.instagram.com/{insta_handle}/"],
            "resultsLimit": 20,
            "resultsType": "posts",
            "commentsMode": "FIRST_PAGE",
            "commentsLimit": 5
        }

        run = apify_client.actor("apify/instagram-scraper").call(run_input=run_input)
        items = apify_client.dataset(run["defaultDatasetId"]).list_items().items

        added_count = 0
        for item in items:
            post_url = item.get("url")
            if not post_url:
                continue

            # 중복 URL 건너뛰기
            existing = posts_ref.where("url", "==", post_url).limit(1).get()
            if len(existing) > 0:
                continue

            # 게시일 변환 (KST)
            ts = item.get("timestamp")
            if ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)
                date_str = dt.strftime("%Y-%m-%d")
                created_ms = int(dt.timestamp() * 1000)
            else:
                now = datetime.now(KST)
                date_str = now.strftime("%Y-%m-%d")
                created_ms = int(now.timestamp() * 1000)

            # 피드 vs 릴스 구분
            post_type = item.get("type", "")
            kind = "reel" if post_type == "Video" or "/reel/" in post_url else "feed"

            # 본문 + 첫 댓글 원문 통합
            caption = item.get("caption") or ""
            first_comment = ""
            comments = item.get("latestComments") or []
            for c in comments:
                if c.get("ownerUsername") == insta_handle:
                    first_comment = c.get("text") or ""
                    break

            raw_text = f"{caption}\n{first_comment}".strip()

            # 분류 없이 원본(rawText) 그대로 저장
            doc_data = {
                "account": acct_code,
                "date": date_str,
                "kind": kind,
                "url": post_url,
                "rawText": raw_text,
                "createdAt": created_ms
            }

            posts_ref.add(doc_data)
            added_count += 1

        print(f"[{insta_handle}] 신규 원본 {added_count}건 저장 완료.")

if __name__ == "__main__":
    sync()
