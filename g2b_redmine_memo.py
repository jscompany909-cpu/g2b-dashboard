"""
g2b_redmine_memo.py — Redmine 리서치 트래커에 설계서 등록
"""
import requests

REDMINE_URL     = "http://192.168.14.19:3000"
REDMINE_AUTH    = ("admin", "11111111")
REDMINE_PROJECT = "g2b_project"
TRACKER_ID      = 8   # 리서치

CONTENT = """## 이노티움 G2B 입찰 인텔리전스 시스템 — 전체 설계서 v3

> 나라장터·KISA·NIPA·IITP 공고를 자동 수집하고, Azure GPT-4o로 채점해
> GitHub Pages 대시보드 / 이메일로 자동 배포하는 파이프라인.
> **PC 없이 매일 오전 9시 GitHub Actions에서 자동 실행.**

---

## 1. 전체 아키텍처

```
[GitHub Actions — 평일 09:00 KST 자동 실행 (PC 불필요)]

  나라장터 G2B API  (최대 10,000건)
  IITP REST API    (최대   600건)  →  g2b_harness.py  →  SQLite DB
  KISA 스크래핑    (약    100건)       수집·채점·검증
  NIPA 스크래핑    (약    100건)
           ↓
  g2b_pdf_budget.py
  Playwright → 공고 PDF/HWPX 다운로드 → 사업금액 추출
           ↓
  g2b_export_json.py --ci
    ├─ DB → announcements.json
    └─ git push → GitHub Pages 자동 갱신
           ↓
  g2b_email_notify.py
    └─ HTML 이메일 → jsbae@innotium.com

[클라우드 — 24시간 열람]
  대시보드: https://jscompany909-cpu.github.io/g2b-dashboard/
  Redmine:  http://192.168.14.19:3000 (사내망 — 로컬 수동 등록)
```

---

## 2. 레포 구조

**단일 레포**: `github.com/jscompany909-cpu/g2b-dashboard`

| 파일 | 역할 |
|------|------|
| `index.html` | 대시보드 UI (GitHub Pages) |
| `data/announcements.json` | 수집 데이터 |
| `정부과제_트렌드_창고.db` | SQLite DB (Actions에서 누적) |
| `g2b_harness.py` | 수집·채점·검증 메인 실행 |
| `g2b_bid_collector.py` | G2B API + Azure GPT-4o 채점 |
| `scrapers.py` | KISA·NIPA·IITP 수집 |
| `g2b_pdf_budget.py` | PDF/HWPX 사업금액 추출 |
| `g2b_export_json.py` | DB→JSON + GitHub push |
| `g2b_email_notify.py` | 주간 HTML 이메일 발송 |
| `requirements.txt` | Python 패키지 목록 |
| `.github/workflows/weekly-collect.yml` | Actions 스케줄 |

---

## 3. 수집 소스

| 출처 | 방식 | 수집량 |
|------|------|--------|
| **G2B** (나라장터) | 공공 Open API | 최대 10,000건 |
| **IITP** | REST API 직접 호출 | 최대 600건 |
| **KISA** | HTML 스크래핑 | 약 100건 |
| **NIPA** | HTML 스크래핑 | 약 100건 |

---

## 4. AI 채점 — Azure GPT-4o

### 벤치마크 (GPT-4o vs o4-mini)

| 항목 | GPT-4o 채택 | o4-mini 탈락 |
|------|------------|-------------|
| 완주 | 20/20건 ✅ | 3/20건 ❌ |
| 일관성 (std≤1) | **100%** | - |
| 평균 std | **0.00** | - |
| 응답 속도 | **2.8초/건** | 3.7초/건 |

### 채점 기준

| 점수 | 기준 |
|------|------|
| 9~10점 | 직접 납품 가능 (DLP, ECM, N2SF, 백업, 랜섬웨어 등) |
| 7~8점  | 보안 솔루션 구축·도입 관련 |
| 5~6점  | 관련 제품 일부 포함 가능성 |
| 2~4점  | 낮은 관련성 |
| 0~1점  | 무관 / 컨설팅·감사·교육·관제 전용 |

---

## 5. 하네스 신뢰도 구조

```
Phase 0  수집 (G2B + IITP + KISA + NIPA)
Round 1~3 반복 (신뢰도 80% 달성 시 조기 종료):
  Phase 1  평가  — 불확실 구간(4~7점) 비율 → 신뢰도 산출
  Phase 2  검증  — 불확실 항목 3회 재채점, std 측정
  Phase 3  반영  — 중앙값으로 DB 업데이트

→ 실제 결과: 신뢰도 96.8%, Round 1 조기 완료
```

---

## 6. PDF/HWPX 사업금액 추출

```
7점 이상 공고 중 budget=0 항목 대상

Playwright → 공고 페이지 접근 (봇 감지 우회)
  → 첨부파일 탐색: .pdf → .hwpx → .hwp 우선순위
  → expect_download()로 파일 캡처
  → PDF: pdfplumber 텍스트 추출
  → HWPX: ZIP 해제 → XML 파싱
  → 금액 패턴 매칭 → DB budget 컬럼 저장
```

### 지원 금액 패턴

- `[추정가격: 54,545,455원]`
- `기초금액 금 42,220,000원(부가세 포함)`
- `금액 : 6,000(천원)` — 천원 단위 자동 변환
- `금39,600,000원` — 독립 패턴

### 실제 추출 결과

| 공고명 | 금액 |
|--------|------|
| 정보시스템 웹서버·백업스토리지 고도화 | 1.4억 |
| 디지털의료제품 전주기 관리시스템 구축 | 4.4억 |
| 2026년 보안솔루션 유지보수 | 1.8억 |
| 과기정통부 N2SF 적용 업무자료 등급분류 | 5,454만원 |
| 자동백업시스템 유지보수 계약 | 4,222만원 |
| 수의계약·사전규격공개 | - (비공개) |

---

## 7. 대시보드 탭 구성

| 탭 | 내용 | 필터 |
|----|------|------|
| 🎯 Top 후보 | 5점+ | **마감 미경과 건만** |
| 📋 전체 신규 | 1점+ | 최근 30일 |
| ⏰ 마감임박 | 점수 무관 | 14일 내 마감 |
| 📊 통계 | 발주처 TOP10, 카테고리 분포 | - |
| 🔍 Raw | 전체 원본 | 최근 30일 |
| 🗑️ 숨김 | 숨긴 항목 | localStorage (브라우저 로컬) |

---

## 8. 이메일 알림

- **발신**: jscompany909@gmail.com
- **수신**: jsbae@innotium.com
- **발송**: GitHub Actions 완료 후 자동

포함 내용: KPI 요약 / Top 후보 테이블(클릭 링크) / 마감임박 / 대시보드 버튼

---

## 9. GitHub Actions 스케줄

| 항목 | 값 |
|------|-----|
| 실행 주기 | **평일(월~금) 09:00 KST** |
| cron | `0 0 * * 1-5` (UTC 기준) |
| 검증 기간 | **~2026-07-13** (이후 자동 스킵) |
| 수동 실행 | workflow_dispatch 버튼 |

```
실행 순서:
1. 날짜 체크 (7/13 이후면 스킵)
2. Python 3.11 + Playwright Chromium 설치
3. .env 생성 (GitHub Secrets)
4. g2b_harness.py — 수집 + GPT-4o 채점 + 검증
5. g2b_pdf_budget.py — PDF/HWPX 금액 추출
6. g2b_export_json.py --ci — JSON + GitHub Pages push
7. git commit + push — DB 갱신
8. g2b_email_notify.py — 결과 이메일 발송
9. (실패 시) 오류 이메일 자동 발송
```

---

## 10. PC 필요 여부

| 기능 | PC 필요 |
|------|---------|
| 대시보드 보기 | ❌ GitHub Pages |
| 이메일 수신 | ❌ 자동 발송 |
| 수집·채점·금액추출 | ❌ GitHub Actions |
| **Redmine 등록** | ✅ 사내망 — 로컬 수동 |

---

## 11. 검증 기간 이후 계속 사용

`.github/workflows/weekly-collect.yml` 에서 아래 줄 삭제 또는 날짜 변경:
```
DEADLINE=20260713
```
"""


def main():
    body = {
        "issue": {
            "project_id":  REDMINE_PROJECT,
            "tracker_id":  TRACKER_ID,
            "subject":     "[설계서 v3] G2B 입찰 인텔리전스 — GitHub Actions 자동화 완성",
            "description": CONTENT,
            "priority_id": 2,
        }
    }
    r = requests.post(
        f"{REDMINE_URL}/issues.json",
        json=body,
        auth=REDMINE_AUTH,
        timeout=10,
    )
    if r.status_code in (200, 201):
        issue = r.json()["issue"]
        print(f"등록 완료: #{issue['id']} — {issue['subject']}")
        print(f"링크: {REDMINE_URL}/issues/{issue['id']}")
    else:
        print(f"오류 {r.status_code}: {r.text[:200]}")


if __name__ == "__main__":
    main()
