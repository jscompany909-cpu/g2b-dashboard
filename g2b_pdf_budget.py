"""
g2b_pdf_budget.py
G2B 7점+ 공고 → 첨부 PDF(공고서 변환본) 클릭 다운로드 → 사업금액 추출 → DB 업데이트

작동 원리:
  Playwright로 G2B 공고 페이지 접근 → '.pdf' 텍스트 클릭 → expect_download() 캡처
  → pdfplumber 텍스트 추출 → 금액 패턴 파싱 → DB 저장
"""

import io
import os
import re
import sqlite3
import subprocess
import sys

import pdfplumber
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "정부과제_트렌드_창고.db")

# 금액 패턴 — 우선순위 순 (추정가격 > 금액원 > 금액천원)
AMOUNT_PATTERNS = [
    (r'\[추정가격\s*:\s*([\d,]+)\s*원\]',              1),   # [추정가격: 54,545,455원]
    (r'추정\s*가격\s*[：:]\s*([\d,]+)\s*원',             1),
    (r'추정가격[^\d]+([\d,]+)\s*원',                     1),
    (r'기초\s*금액\s*금\s*([\d,]+)\s*원',                1),   # 기초금액 금 42,220,000원
    (r'금액\s*[：:]\s*([\d,]+)\s*원',                    1),
    (r'금액\s*[：:]\s*([\d,]+)\s*\(천원\)',         1_000),
    (r'금액\s*[：:]\s*([\d,]+)\s*천\s*원',          1_000),
    (r'사업\s*금액\s*[：:]\s*([\d,]+)\s*\(천원\)',  1_000),
    (r'기초\s*금액\s*[：:]\s*([\d,]+)\s*원',              1),
    (r'예산\s*액?\s*[：:]\s*([\d,]+)\s*원',               1),
    (r'(?<!\S)금\s*([\d,]{6,})\s*원',                    1),   # 금39,600,000원 (독립)
]


def parse_amount(text: str) -> int:
    for pat, mult in AMOUNT_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            try:
                val = int(m.group(1).replace(',', '')) * mult
                if 500_000 <= val <= 1_000_000_000_000:
                    return val
            except ValueError:
                continue
    return 0


def fmt(amount: int) -> str:
    if amount >= 100_000_000:
        return f"{amount/100_000_000:.1f}억"
    if amount >= 1_000_000:
        return f"{amount//1_000:,}천원"
    return f"{amount:,}원"


def fetch_amount_from_pdf(bid_link: str) -> int:
    """Playwright → PDF 클릭 다운로드 → 금액 추출"""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            accept_downloads=True,
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        try:
            page.goto(bid_link, wait_until="networkidle", timeout=40_000)
            page.wait_for_timeout(4_000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3_000)

            # PDF 요소 찾기 — '.pdf' 텍스트 포함 첫 번째 요소
            pdf_count = page.locator("text=.pdf").count()
            if pdf_count == 0:
                print("    첨부 PDF 없음")
                return 0

            pdf_loc = page.locator("text=.pdf").first
            pdf_name = (pdf_loc.text_content() or "").strip()
            print(f"    PDF 발견: {pdf_name[:60]}")

            # 다운로드 캡처
            with page.expect_download(timeout=30_000) as dl_info:
                pdf_loc.click()
            download = dl_info.value
            print(f"    다운로드: {download.suggested_filename} "
                  f"({os.path.getsize(download.path()):,} bytes)")

            # pdfplumber 텍스트 추출 → 금액 파싱
            with open(download.path(), "rb") as f:
                pdf_bytes = f.read()
            download.delete()

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pg in pdf.pages[:6]:
                    text = pg.extract_text() or ""
                    amount = parse_amount(text)
                    if amount > 0:
                        return amount

            print("    금액 패턴 없음")
            return 0

        except Exception as e:
            print(f"    오류: {e}")
            return 0
        finally:
            browser.close()


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    targets = conn.execute(
        "SELECT id, title, link FROM announcements "
        "WHERE score >= 7 AND (budget IS NULL OR budget = 0) "
        "AND source = 'G2B' AND (is_deleted IS NULL OR is_deleted=0) "
        "ORDER BY score DESC"
    ).fetchall()

    print(f"PDF 금액 추출 대상: {len(targets)}건 (7점+, G2B, 금액 미수집)")
    updated = 0

    for row in targets:
        rid, title, link = row["id"], row["title"], row["link"]
        print(f"\n[{rid}] {title[:55]}")

        if not link:
            print("    링크 없음")
            continue

        amount = fetch_amount_from_pdf(link)

        if amount > 0:
            conn.execute(
                "UPDATE announcements SET budget=? WHERE id=?", (amount, rid)
            )
            conn.commit()
            updated += 1
            print(f"    ✅ {fmt(amount)}")
        else:
            print("    ─ 금액 미공개 (수의계약/협상)")

    conn.close()
    print(f"\n완료: {updated}/{len(targets)}건 금액 수집")

    # --no-export: GitHub Actions에서 export는 별도 step으로 처리
    if updated > 0 and "--no-export" not in sys.argv:
        print("\n대시보드 업데이트 중...")
        subprocess.run([sys.executable, "g2b_export_json.py", "-y"], check=False)


if __name__ == "__main__":
    run()
