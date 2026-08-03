import datetime
import os
import sqlite3
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from dotenv import load_dotenv

# .env 환경 변수 로드
load_dotenv()

# =====================================================================
# 🖥️ [온프레미스 고정 설정]
# =====================================================================

G2B_API_KEY = os.environ.get("G2B_API_KEY")

def is_relevant(title):
    t = title.lower()
    return any(kw.lower() in t for kw in INNOTIUM_KEYWORDS)


# 104개 사내 타겟 보안 매트릭스 키워드
INNOTIUM_KEYWORDS = [
    "HACKER", "RANSOM", "랜섬웨어", "랜섬웨어검사", "랜섬웨어탐지", "랜섬웨어탐지차단",
    "해킹", "랜섬웨어방지솔루션", "랜섬웨어차단", "컴퓨터바이러스", "해킹방지", "랜섬웨어방어",
    "BACKUP", "SMB", "데이터백업", "데이터이관", "랜섬웨어백업", "랜섬웨어백업솔루션",
    "문서복구", "문서이관", "백업", "백업솔루션", "백업파일", "보안백업", "자동백업",
    "컴퓨터데이터백업", "컴퓨터백업", "클라우드백업", "파일백업", "비가시성워터마크",
    "비가시성워터마크솔루션", "워터마크", "캡처방지", "프린트워터마크", "화면보안", "화면캡처",
    "화면캡처방지", "화면캡쳐", "화면캡쳐방지", "DLP", "화면워터마크", "DLP솔루션", "출력물보안",
    "출력물워터마크", "화면보안솔루션", "스마트워크", "DRM", "DRM솔루션", "N2SF", "N²SF",
    "제로트러스트", "Zero Trust", "ZTA", "개인정보", "개인정보등급분류", "개인정보보안솔루션",
    "개인정보보호", "개인정보보호솔루션", "개인정보전주기", "개인정보전주기관리", "기술정보전주기",
    "데이터등급분류", "데이터반출", "데이터암호화", "문서등급분류", "문서반출", "문서암호화",
    "문서외부반출", "문서외부반출시스템", "문서전주기", "문서전주기관리", "반출시스템", "암호화",
    "암호화솔루션", "외부반출", "전주기", "전주기관리", "AIECM", "AI문서중앙화", "ECM", "ECM문서관리",
    "문서보안솔루션", "문서보안프로그램", "문서중앙화", "문서중앙화솔루션", "문서협업솔루션", "이노티움문서중앙화",
    "노트북보안", "데이터유출방지", "시스템보호", "엔드포인트보안", "이노스마트플랫폼", "기업보안프로그램",
    "데이터보안", "보안솔루션", "통합보안솔루션", "SBOM", "공급망보안", "소프트웨어공급망",
    "정보보호솔루션", "정보보호시스템", "사이버보안솔루션",
]


def ask_azure_gpt4o_scoring(announcement_title):
    """Azure GPT-4o 채점 — 이노티움 납품 가능성 0~10점"""
    import re
    from openai import AzureOpenAI

    if not os.environ.get("AZURE_OPENAI_ENDPOINT") or not os.environ.get("AZURE_OPENAI_KEY"):
        return 0, "미설정", "Azure 환경변수 없음"

    client = AzureOpenAI(
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION"),
    )
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")

    prompt = f"""당신은 이노티움(Innotium) 사업개발팀 전문가입니다.

[이노티움 사업 특성 — 핵심]
이노티움은 보안 소프트웨어/솔루션을 '납품·구축'하는 기업입니다.
컨설팅·감사·관제·진단·교육은 우리 사업이 아닙니다.

[납품 가능한 핵심 제품군]
- 문서중앙화(ECM): 문서 보안·관리·협업 솔루션 구축
- DLP/정보유출방지: 화면보안, 출력물보안, 워터마크, 캡처방지 시스템
- 랜섬웨어 탐지·차단 및 보안백업 솔루션
- 제로트러스트/N2SF 아키텍처 구축
- SBOM/소프트웨어 공급망 보안 솔루션
- 개인정보보호 솔루션 (등급분류·암호화·전주기 관리 시스템)
- AI 기반 업무혁신(AX) 플랫폼

[채점 기준]
9~10점 : 이노티움 제품 직접 납품 가능 (문서중앙화 구축, DLP 도입, 랜섬웨어 방어시스템 등)
7~8점  : 보안 솔루션·시스템 구축·도입 관련
5~6점  : 관련 제품 일부 포함 가능성
2~4점  : 낮은 관련성
0~1점  : 무관하거나 컨설팅/감사/관제/교육/진단 전용

[반드시 0~1점 처리]
- 보안 컨설팅, 보안 감사, 취약점 진단, 모의해킹
- 보안 관제 서비스, 침해사고 대응
- 정보보호 인증 지원, 보안 교육·훈련
- 개인정보영향평가(PIA), 감리 전용

[평가 대상 공고]
{announcement_title}

아래 형식으로만 출력하세요:
SCORE: 숫자
TAG: 키워드
REASON: 한줄이유"""

    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=100,
        )
        text = resp.choices[0].message.content.strip()
        score, tag, reason = 0, "기타", "-"
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("SCORE:"):
                m = re.search(r"\b(\d+)\b", line)
                score = min(10, max(0, int(m.group(1)))) if m else 0
            elif line.startswith("TAG:"):
                tag = line.split(":", 1)[1].strip()
            elif line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
        return score, tag, reason
    except Exception as e:
        return 0, "오류", str(e)[:50]


# 하위 호환 — harness 등 기존 import 유지
ask_local_qwen_scoring = ask_azure_gpt4o_scoring


def _fetch_page(page_num, base_params, url, headers):
    """단일 페이지 수집 (병렬 워커용)"""
    params = {**base_params, "pageNo": str(page_num)}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20, verify=True)
        if response.status_code != 200:
            return page_num, []
        root = ET.fromstring(response.text)
        if root.findtext(".//resultCode", "") != "00":
            return page_num, []
        items = [
            {child.tag: (child.text or "") for child in item_el}
            for item_el in root.findall(".//item")
        ]
        if page_num == 1 and items:
            print(f"  [필드목록] {list(items[0].keys())[:15]}")
        return page_num, items
    except Exception as e:
        print(f"⚠️ [{page_num}페이지] 오류: {e}")
        return page_num, []


def fetch_g2b_raw_data():
    """나라장터 용역 입찰공고 병렬 수집 (5개 페이지 동시 요청)"""
    url = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServcPPSSrch"
    today = datetime.date.today()
    base_params = {
        "serviceKey": requests.utils.unquote(G2B_API_KEY),
        "numOfRows": "100",
        "inqryDiv": "1",
        "inqryBgnDt": (today - datetime.timedelta(days=30)).strftime("%Y%m%d"),
        "inqryEndDt": today.strftime("%Y%m%d"),
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    MAX_PAGES = 100  # 100페이지 × 100건 = 최대 10000건

    # 1페이지 먼저 받아서 데이터 있는지 확인
    _, first_items = _fetch_page(1, base_params, url, headers)
    if not first_items:
        return []

    all_items = list(first_items)

    if len(first_items) < 100:
        return all_items  # 1페이지뿐

    # 2~MAX_PAGES 병렬 수집 (동시 5개)
    done = False
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_fetch_page, p, base_params, url, headers): p
            for p in range(2, MAX_PAGES + 1)
        }
        for future in as_completed(futures):
            try:
                page_num, items = future.result()
            except Exception:
                # 취소된 future 또는 오류 — 스킵
                continue
            if done:
                continue
            if items:
                all_items.extend(items)
                print(f"  ├─ {page_num}페이지 {len(items)}건 수신")
            if len(items) < 100:
                # 마지막 페이지 도달 — 나머지 취소 후 루프 종료
                done = True
                for f in futures:
                    f.cancel()

    return all_items


def run_innotium_reinforced_pipeline():
    print("🚀 [이노티움 데이터 센터] 가동...")

    if not G2B_API_KEY:
        print("❌ [중단] .env 파일에 G2B_API_KEY를 설정해 주세요.")
        return

    from scrapers import fetch_all_external

    db_filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), "정부과제_트렌드_창고.db")
    conn = sqlite3.connect(db_filename)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution TEXT,
            title TEXT UNIQUE,
            reg_date TEXT,
            end_date TEXT,
            link TEXT,
            score INTEGER,
            category_tag TEXT,
            ai_reason TEXT,
            source TEXT DEFAULT 'G2B',
            budget INTEGER DEFAULT 0
        )
    """)
    # 기존 DB 컬럼 마이그레이션
    existing_cols = [r[1] for r in cursor.execute("PRAGMA table_info(announcements)").fetchall()]
    if "source" not in existing_cols:
        cursor.execute("ALTER TABLE announcements ADD COLUMN source TEXT DEFAULT 'G2B'")
    if "budget" not in existing_cols:
        cursor.execute("ALTER TABLE announcements ADD COLUMN budget INTEGER DEFAULT 0")
    conn.commit()

    # ── 1. 나라장터 G2B 수집 ──────────────────────────────────────────────────
    print("📡 나라장터 G2B 수집 중...")
    g2b_items = fetch_g2b_raw_data()
    print(f" └─ G2B: {len(g2b_items)}건")
    for item in g2b_items:
        item.setdefault("source", "G2B")

    # ── 2. 외부 사이트 수집 (KISA, NIPA, IITP) ────────────────────────────────
    print("📡 외부 사이트 수집 중 (KISA / NIPA / IITP)...")
    ext_items = fetch_all_external(pages=10)
    print(f" └─ 외부: {len(ext_items)}건")

    all_items = g2b_items + ext_items
    print(f"📦 전체 수집: {len(all_items)}건 (G2B {len(g2b_items)} + 외부 {len(ext_items)})")

    if not all_items:
        print("⚠️ 수집된 항목이 없습니다.")
        conn.close()
        return

    # ── 3. 키워드 필터 + 중복 제거 ───────────────────────────────────────────
    already_in_db = 0
    keyword_match = 0
    candidates = []
    for item in all_items:
        title = item.get("bidNtceNm", "").strip()
        if not title:
            continue
        cursor.execute("SELECT 1 FROM announcements WHERE title = ?", (title,))
        if cursor.fetchone():
            already_in_db += 1
            continue
        if is_relevant(title):
            keyword_match += 1
            candidates.append(item)

    print(f"🔎 전체: {len(all_items)}건 → 키워드 매칭: {keyword_match}건 → 중복 스킵: {already_in_db}건 → 신규 채점: {len(candidates)}건")

    if not candidates:
        print("✅ 신규 항목 없음")
        conn.close()
        return

    # ── 4. Qwen 병렬 채점 ────────────────────────────────────────────────────
    def score_item(item):
        title = item.get("bidNtceNm", "").strip()
        score, tag, reason = ask_local_qwen_scoring(title)
        return item, score, tag, reason

    new_inserted_count = 0
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(score_item, item): item for item in candidates}
        for future in as_completed(futures):
            item, score, tag, reason = future.result()
            title       = item.get("bidNtceNm", "").strip()
            institution = item.get("ntceInsttNm", "").strip() or "기관미상"
            source      = item.get("source", "G2B")
            reg_date    = item.get("bidNtceDt", "")[:10] if item.get("bidNtceDt") else "-"
            end_date_raw = item.get("bidClsDt") or item.get("bidClseDateTime") or item.get("bidClseDt") or ""
            end_date    = end_date_raw[:10] if end_date_raw.strip() else "-"
            link        = item.get("bidNtceDtlUrl", "")
            # 추정가격(presmptPrce) → 없으면 배정예산액(asignBdgtAmt)
            budget_raw  = item.get("presmptPrce") or item.get("asignBdgtAmt") or "0"
            try:
                budget = int(float(str(budget_raw).replace(",", ""))) if str(budget_raw).strip() else 0
            except (ValueError, TypeError):
                budget = 0

            cursor.execute("""
                INSERT OR IGNORE INTO announcements
                (institution, title, reg_date, end_date, link, score, category_tag, ai_reason, source, budget)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (institution, title, reg_date, end_date, link, score, tag, reason, source, budget))

            if cursor.rowcount > 0:
                new_inserted_count += 1
                print(f"📊 [{new_inserted_count}건] [{source}] [{tag}] {score}점 → {title}")

    conn.commit()
    conn.close()
    print("=" * 60)
    print(f"✨ 신규 적재: {new_inserted_count}건")
    print(f"✅ 완료.")


if __name__ == "__main__":
    run_innotium_reinforced_pipeline()
