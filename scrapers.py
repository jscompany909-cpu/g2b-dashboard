"""
이노티움 외부 공고 수집기
- KISA: requests + BeautifulSoup (HTML 테이블)
- NIPA: requests + BeautifulSoup (HTML 테이블)
- IITP: requests + BeautifulSoup (HTML 테이블, 여러 게시판 시도)
"""

import re
import datetime
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
TIMEOUT = 15


# ── 날짜 정규화 ────────────────────────────────────────────────────────────────
def _parse_date(raw: str) -> str:
    """여러 형식의 날짜 문자열을 YYYY-MM-DD로 통일"""
    raw = raw.strip()
    patterns = [
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",   # 2026.05.04 / 2026-05-04
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",     # 2026년 5월 4일
        r"(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})",   # 26.05.04
    ]
    for pat in patterns:
        m = re.search(pat, raw)
        if m:
            y, mo, d = m.groups()
            y = "20" + y if len(y) == 2 else y
            return f"{y}-{int(mo):02d}-{int(d):02d}"
    return raw[:10] if len(raw) >= 10 else "-"


def _normalize(title, institution, reg_date, end_date, link, source):
    return {
        "bidNtceNm":    title.strip(),
        "ntceInsttNm":  institution,
        "bidNtceDt":    _parse_date(reg_date),
        "bidClsDt":     _parse_date(end_date) if end_date.strip() else "-",
        "bidNtceDtlUrl": link,
        "source":       source,
    }


# ── KISA ──────────────────────────────────────────────────────────────────────
def fetch_kisa(max_pages: int = 10) -> list:
    """
    KISA 입찰공고 수집
    URL: https://www.kisa.or.kr/403?page=N
    컬럼: 번호 | 제목 | 날짜(YYYY-MM-DD) | 조회수 | 첨부
    """
    base   = "https://www.kisa.or.kr"
    items  = []

    for page in range(1, max_pages + 1):
        try:
            url = f"{base}/403?page={page}"
            r   = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            soup = BeautifulSoup(r.text, "html.parser")
            table = soup.select_one("table")
            if not table:
                break

            rows = table.select("tr")[1:]  # 헤더 제외
            if not rows:
                break

            page_count = 0
            for row in rows:
                cells  = row.select("td")
                link_el = row.select_one("a")
                if not link_el or len(cells) < 3:
                    continue

                title = link_el.get_text(strip=True)
                if not title or title.isdigit() or len(title) < 5:
                    continue

                href     = link_el.get("href", "")
                full_url = href if href.startswith("http") else base + href
                # cells: [번호, 제목, 날짜, 조회수, 첨부]
                reg_date = cells[2].get_text(strip=True) if len(cells) > 2 else "-"

                items.append(_normalize(title, "KISA", reg_date, "-", full_url, "KISA"))
                page_count += 1

            print(f"  KISA {page}페이지: {page_count}건")

            # 다음 페이지 링크 없으면 종료
            pager = soup.select(".pager a, .pagination a")
            next_pages = [a for a in pager if a.get_text(strip=True).isdigit()
                          and int(a.get_text(strip=True)) > page]
            if not next_pages and page_count < 5:
                break

        except Exception as e:
            print(f"  ⚠️ KISA {page}페이지 오류: {e}")
            break

    return items


# ── NIPA ──────────────────────────────────────────────────────────────────────
def fetch_nipa(max_pages: int = 10) -> list:
    """
    NIPA 입찰공고 수집
    URL: https://www.nipa.kr/home/2-3
    컬럼: 번호 | 제목 | 작성자 | 첨부 | 조회수 | 날짜
    """
    base  = "https://www.nipa.kr"
    items = []

    for page in range(1, max_pages + 1):
        try:
            # NIPA는 페이지 파라미터 없이 모든 항목을 한 번에 보여주는 구조
            # pageIndex 파라미터로 시도
            url  = f"{base}/home/2-3" + (f"?pageIndex={page}" if page > 1 else "")
            r    = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            soup = BeautifulSoup(r.text, "html.parser")
            table = soup.select_one("table")
            if not table:
                break

            rows = table.select("tr")[1:]
            if not rows:
                break

            # 첫 번째 행의 번호로 중복 감지
            first_num = rows[0].select("td")[0].get_text(strip=True) if rows[0].select("td") else ""

            page_count = 0
            seen_on_page = []
            for row in rows:
                cells   = row.select("td")
                link_el = row.select_one("a")
                if not link_el or len(cells) < 5:
                    continue

                title = link_el.get_text(strip=True)
                if not title or title.isdigit() or len(title) < 5:
                    continue

                href     = link_el.get("href", "")
                full_url = href if href.startswith("http") else base + href
                # cells: [번호, 제목, 작성자, 첨부, 조회수, 날짜]
                reg_date = cells[5].get_text(strip=True) if len(cells) > 5 else "-"
                row_num  = cells[0].get_text(strip=True)
                seen_on_page.append(row_num)
                items.append(_normalize(title, "NIPA", reg_date, "-", full_url, "NIPA"))
                page_count += 1

            print(f"  NIPA {page}페이지: {page_count}건")

            # NIPA는 실질적으로 1페이지만 있으므로 중복이면 중단
            if page > 1 and first_num in [i["bidNtceNm"][:5] for i in items[:5]]:
                break
            if page_count < 8:
                break

        except Exception as e:
            print(f"  ⚠️ NIPA {page}페이지 오류: {e}")
            break

    return items


# ── IITP ──────────────────────────────────────────────────────────────────────
def fetch_iitp(max_pages: int = 10) -> list:
    """
    IITP 사업공고 수집 — requests 직접 API 호출 (Playwright 불필요)
    API: POST https://www.iitp.kr/link-svc/api/iris/list.do
    세션으로 쿠키를 먼저 획득한 뒤 JSON API를 호출한다.
    """
    BASE    = "https://www.iitp.kr"
    API_URL = f"{BASE}/link-svc/api/iris/list.do"
    LIST_URL = f"{BASE}/web/lay1/program/S1T44C51/iris/list.do"
    items   = []

    session = requests.Session()
    session.headers.update(HEADERS)

    # 쿠키/세션 토큰 초기화
    try:
        session.get(LIST_URL, timeout=TIMEOUT)
    except Exception as e:
        print(f"  ⚠️ IITP 세션 초기화 실패: {e}")

    for page_num in range(1, max_pages + 1):
        payload = {
            "cms_menu_seq": "51",
            "cpage":        page_num,
            "rows":         20,
            "keyword":      "",
            "condition":    "",
            "sort":         "latest",
        }
        try:
            r = session.post(
                API_URL,
                json=payload,
                headers={**HEADERS,
                         "Content-Type": "application/json",
                         "Referer": LIST_URL,
                         "X-Requested-With": "XMLHttpRequest"},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            data      = r.json()
            rows_data = data.get("list", [])

            if not rows_data:
                break

            for row in rows_data:
                title = row.get("title", "").strip()
                if not title:
                    continue
                item_id  = row.get("id", "")
                reg_date = row.get("receipt_begin_date", "-")
                end_date = row.get("receipt_end_date", "-")
                link     = f"{BASE}/web/lay1/program/S1T44C51/iris/view.do?id={item_id}"
                items.append(_normalize(title, "IITP", reg_date, end_date, link, "IITP"))

            total_page = data.get("pagination", {}).get("totalpage", 1)
            print(f"  IITP {page_num}/{total_page}페이지: {len(rows_data)}건")

            if page_num >= total_page:
                break

        except Exception as e:
            print(f"  ⚠️ IITP {page_num}페이지 오류: {e}")
            # requests 실패 시 Playwright로 폴백
            if page_num == 1:
                print("  → Playwright 폴백 시도...")
                return _fetch_iitp_playwright(max_pages)
            break

    return items


def _fetch_iitp_playwright(max_pages: int = 10) -> list:
    """IITP 폴백: Playwright 사용 (requests 실패 시)"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⚠️ IITP: playwright 미설치 → 수집 건너뜀")
        return []

    BASE  = "https://www.iitp.kr"
    items = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg      = browser.new_page()
        pg.goto(f"{BASE}/web/lay1/program/S1T44C51/iris/list.do",
                wait_until="networkidle", timeout=25000)
        pg.wait_for_timeout(1000)

        for page_num in range(1, max_pages + 1):
            try:
                payload = {"cms_menu_seq": "51", "cpage": page_num, "rows": 20,
                           "keyword": "", "condition": "", "sort": "latest"}
                data = pg.evaluate("""
                    async (payload) => {
                        const r = await fetch('https://www.iitp.kr/link-svc/api/iris/list.do', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });
                        return await r.json();
                    }
                """, payload)

                rows_data = data.get("list", [])
                if not rows_data:
                    break

                for row in rows_data:
                    title = row.get("title", "").strip()
                    if not title:
                        continue
                    item_id  = row.get("id", "")
                    reg_date = row.get("receipt_begin_date", "-")
                    end_date = row.get("receipt_end_date", "-")
                    link     = f"{BASE}/web/lay1/program/S1T44C51/iris/view.do?id={item_id}"
                    items.append(_normalize(title, "IITP", reg_date, end_date, link, "IITP"))

                print(f"  IITP(PW) {page_num}페이지: {len(rows_data)}건")
                total_page = data.get("pagination", {}).get("totalpage", 1)
                if page_num >= total_page:
                    break
            except Exception as e:
                print(f"  ⚠️ IITP(PW) {page_num}페이지 오류: {e}")
                break

        browser.close()
    return items


# ── 전체 수집 ──────────────────────────────────────────────────────────────────
def fetch_all_external(pages: int = 10) -> list:
    """KISA + NIPA + IITP 통합 수집"""
    all_items = []

    print("🔍 KISA 입찰공고 수집 중...")
    kisa = fetch_kisa(pages)
    print(f"  └─ KISA 총 {len(kisa)}건")
    all_items.extend(kisa)

    print("🔍 NIPA 입찰공고 수집 중...")
    nipa = fetch_nipa(pages)
    print(f"  └─ NIPA 총 {len(nipa)}건")
    all_items.extend(nipa)

    print("🔍 IITP 사업공고 수집 중...")
    iitp = fetch_iitp(pages)
    print(f"  └─ IITP 총 {len(iitp)}건")
    all_items.extend(iitp)

    return all_items


if __name__ == "__main__":
    items = fetch_all_external(pages=3)
    print(f"\n총 수집: {len(items)}건")
    for i in items[:5]:
        print(f"  [{i['source']}] {i['bidNtceNm'][:50]} | {i['bidNtceDt']}")
