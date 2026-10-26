import os
import re
import json
import datetime
import unicodedata
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, firestore
from apify_client import ApifyClient

# 1. Firebase Admin SDK 초기화
cred_json_str = os.environ.get("FIREBASE_CREDENTIALS_JSON")
cred_dict = json.loads(cred_json_str)
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# 2. Apify 클라이언트 초기화
apify_client = ApifyClient(os.environ.get("APIFY_API_TOKEN"))

ACCOUNTS = {
    "genie_music": "genie",
    "melon_music": "melon"
}

def normalize_text(text):
    if not text:
        return ""
    return unicodedata.normalize('NFKD', text).lower()

def classify_series(acct_code, full_text):
    norm = normalize_text(full_text)
    no_space = norm.replace(" ", "")

    # 지니뮤직 분류 규칙
    if acct_code == 'genie':
        if '스트림파티' in no_space or 'streamparty' in no_space or '채팅' in norm:
            return '스트림파티'
        if 'inside' in norm or '인사이드' in norm:
            if '만나보세요' in norm:
                return '지니인사이드, 옥외광고'
            return '지니인사이드'
        if '#지니에_왔지니' in full_text or '지니에왔지니' in no_space:
            return '지니에 왔지니'
        if '#러닝' in full_text or '러닝' in norm:
            return '러닝'
        return '기타'

    # 멜론 분류 규칙
    elif acct_code == 'melon':
        if '#mma2026' in norm or 'mma2026' in no_space or '#mma' in norm:
            return 'MMA'
        if '스포트라이트' in norm or 'spotlight' in norm:
            return '스포트라이트'
        if '하이라이징' in norm or 'hi-rising' in norm:
            return '하이라이징'
        if 'datalab' in no_space:
            return 'Data Lab'
        if 'global-k' in norm or 'globalkchart' in no_space:
            return 'Global-K Chart'
        if '뮤직웨이브' in norm or 'musicwave' in no_space:
            return '뮤직웨이브'
        if '#아뭘듣' in full_text or '아뭘듣' in norm:
            return '아뭘듣'
        if 'stage99' in no_space or 'stage 99' in norm or 'the sound' in norm or 'thesound' in no_space:
            return '오프라인'
        return '기타'

    return '기타'

def extract_owner_comments(item, target_username):
    owner_comments = []
    comments = item.get("latestComments") or item.get("comments") or []
    for c in comments:
        commenter = (c.get("ownerUsername") or c.get("username") or "").lower()
        if commenter == target_username.lower():
            text = c.get("text") or ""
            if text:
                owner_comments.append(text)
    return " ".join(owner_comments)

def extract_artist(full_text):
    bracket = re.search(r'\[([^\]]+)\]', full_text)
    if bracket:
        return bracket.group(1).strip()
    tags = re.findall(r'#([가-힣a-zA-Z0-9_]+)', full_text)
    ignore_tags = {'지니뮤직', '멜론', 'geniemusic', 'melon', '릴스', '음악추천', 'kpop', '플리', '신보', '컴백'}
    for tag in tags:
        if tag.lower() not in ignore_tags:
            return tag
    return ""

def sync():
    posts_ref = db.collection("posts")
    kst = ZoneInfo("Asia/Seoul")

    for insta_handle, acct_code in ACCOUNTS.items():
        print(f"[{insta_handle}] 게시물 수집 시작...")

        run_input = {
            "directUrls": [f"https://www.instagram.com/{insta_handle}/"],
            "resultsLimit": 800,
            "onlyPostsNewerThan": "2026-01-01",
            "resultsType": "posts",
            "commentsMode": "FIRST_PAGE",
            "commentsLimit": 5
        }

        run = apify_client.actor("apify/instagram-scraper").call(run_input=run_input)
        dataset_items = apify_client.dataset(run["defaultDatasetId"]).iterate_items()

        for item in dataset_items:
            post_url = item.get("url") or f"https://www.instagram.com/p/{item.get('shortCode')}/"
            
            existing = posts_ref.where("url", "==", post_url).limit(1).get()
            if len(existing) > 0:
                continue

            caption = item.get("caption") or ""
            owner_comments = extract_owner_comments(item, insta_handle)
            search_pool = f"{caption} {owner_comments}"

            timestamp_str = item.get("timestamp")
            if timestamp_str:
                dt = datetime.datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).astimezone(kst)
                date_str = dt.strftime("%Y-%m-%d")
            else:
                date_str = datetime.datetime.now(kst).strftime("%Y-%m-%d")

            is_reel = "reel" in post_url.lower() or item.get("type") == "Video"
            kind = "reel" if is_reel else "feed"

            series = classify_series(acct_code, search_pool)
            artist = extract_artist(search_pool)

            new_doc = {
                "account": acct_code,
                "date": date_str,
                "kind": kind,
                "series": series,
                "artist": artist,
                "url": post_url,
                "memo": caption[:250].strip(),
                "createdAt": int(datetime.datetime.now().timestamp() * 1000)
            }
            posts_ref.add(new_doc)
            print(f"  + 추가 완료: {date_str} [{kind}] {series} - {post_url}")

if __name__ == "__main__":
    sync()
