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
    # 1. 특수 영문/기호를 일반 텍스트로 분해 (NFKD)
    decomposed = unicodedata.normalize('NFKD', text)
    # 2. 쪼개진 한글 자모를 다시 정상적인 완성형 한글로 결합 (NFC)
    composed = unicodedata.normalize('NFC', decomposed)
    return composed.lower()

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
        if '스포트라이트' in norm or 'spotlight' in norm or '#스포트라이트' in norm:
            return '스포트라이트'
        if '하이라이징' in norm or 'hi-rising' in norm or '#하이라이징' in norm:
            return '하이라이징'
        if 'datalab' in no_space:
            return 'Data Lab'
        if 'global-k' in norm or 'globalkchart' in no_space:
            return 'Global-K Chart'
        if '뮤직웨이브' in norm or '뮤직웨이브에' in norm or 'musicwave' in no_space:
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
    if not full_text:
        return ""

    # 제외할 단어/태그 목록 (소문자 기준)
    IGNORE_TERMS = {
        'single', 'album', 'mini album', 'ep', 'ep album', '정규', '싱글', '미니앨범',
        'ost', 'track', 'title', '신보', '컴백', '발매', '이벤트', '독점', '안내', '공지',
        'mv', 'teaser', 'interview', 'live', '선공개', 'preview', '포토', '화보',
        '지니뮤직', '멜론', 'geniemusic', 'melon', '릴스', '음악추천', 'kpop', '플리',
        '스트림파티', '지니인사이드', '스포트라이트', '하이라이징', '뮤직웨이브', '아뭘듣',
        'datalab', 'mma', 'mma2026', 'inside', 'only', 'with'
    }

    # 1. 문장 맨 첫머리가 해시태그 아티스트인 경우 (예: #RESCENE 의 4번째 미니 앨범...)
    m_head_tag = re.search(r'^\s*#([A-Za-z0-9가-힣_]+)', full_text)
    if m_head_tag:
        tag = m_head_tag.group(1).strip()
        if tag.lower() not in IGNORE_TERMS:
            return tag

    # 2. "~의 (숫자번째/새/첫) 앨범" 또는 "~의 독점/스토리" 문형 감지
    # 예: "#RESCENE 의 4번째 미니 앨범", "ALLDAY PROJECT의 The 2nd EP"
    m_possessive = re.search(r'#?([A-Za-z0-9가-힣\s&_]+?)\s*의\s+(?:the|새|첫|신곡|신보|\d|독점|미니|정규)', full_text, re.IGNORECASE)
    if m_possessive:
        candidate = m_possessive.group(1).strip()
        if candidate.lower() not in IGNORE_TERMS and len(candidate) <= 30:
            return candidate

    # 3. 본문 내 특수 영문 정규화 후 "with 아티스트" 패턴 (예: with RESCENE)
    m_with = re.search(r'\bwith\s+([A-Za-z0-9가-힣]+)', full_text, re.IGNORECASE)
    if m_with:
        cand = m_with.group(1).strip()
        if cand.lower() not in IGNORE_TERMS:
            return cand

    # 4. 해시태그 목록 중 우선 탐색 (지니인사이드, 이벤트 태그 등 제외)
    tags = re.findall(r'#([가-힣a-zA-Z0-9_]+)', full_text)
    for tag in tags:
        t_low = tag.lower()
        # INSIDE_RESCENE 같은 결합형 태그 처리
        if t_low.startswith('inside_'):
            return tag[7:].strip()
        if t_low not in IGNORE_TERMS and not any(term in t_low for term in ['inside', 'genie', 'melon', 'pulse']):
            return tag

    # 5. "아티스트명 - 곡명" 형태
    m_hyphen = re.search(r'([A-Za-z0-9가-힣\s&]+?)\s*-\s*[A-Za-z0-9가-힣\'"〈<]', full_text)
    if m_hyphen:
        candidate = m_hyphen.group(1).strip()
        if '\n' not in candidate and candidate.lower() not in IGNORE_TERMS and 2 <= len(candidate) <= 25:
            return candidate

    # 6. 최후의 수단: 대괄호 [ ... ] (단, 앨범/곡명일 확률이 높으므로 뒤로 배치)
    brackets = re.findall(r'\[([^\]]+)\]', full_text)
    for b in brackets:
        b_clean = b.strip().replace('#', '')
        if '-' in b_clean:
            b_clean = b_clean.split('-')[0].strip()
        if b_clean.lower() not in IGNORE_TERMS and not any(term in b_clean.lower() for term in ['album', 'single', 'ep', 'ost', '발매', 'pulse', 'crash']):
            return b_clean

    return ""

def sync():
    posts_ref = db.collection("posts")
    kst = ZoneInfo("Asia/Seoul")

    for insta_handle, acct_code in ACCOUNTS.items():
        print(f"[{insta_handle}] 게시물 수집 시작...")

        run_input = {
            "directUrls": [f"https://www.instagram.com/{insta_handle}/"],
            "resultsLimit": 20,
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
