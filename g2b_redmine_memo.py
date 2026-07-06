"""
g2b_redmine_memo.py — Redmine 리서치 트래커에 설계서 등록
"""
import requests

REDMINE_URL     = "http://192.168.14.19:3000"
REDMINE_AUTH    = ("admin", "11111111")
REDMINE_PROJECT = "g2b_project"
TRACKER_ID      = 8   # 리서치

CONTENT = """## 이노티움 G2B 입찰 인텔리전스 시스템 — 전체 설계서 (v2)

> 나라장터·KISA·NIPA·IITP 공고를 자동 수집하고, Azure GPT-4o로 채점해 GitHub Pages 대시보드와 Redmine에 자동 등록하는 파이프라인. PC 없이도 대시보드 열람 가능.

---

## 1. 전체 아키텍처

```
[PC — 수집·채점 단계]
  ┌──────────────────────────────────────────────────────┐
  │  나라장터 G2B API  (최대 10,000건, 병렬 5 스레드)      │
  │  IITP REST API    (최대 600건, requests 직접 호출)     │  → g2b_harness.py
  │  KISA 스크래핑    (약 100건, BeautifulSoup)           │     수집 → 키워드 필터
  │  NIPA 스크래핑    (약 100건, BeautifulSoup)           │     → GPT-4o 채점
  └──────────────────────────────────────────────────────┘
               ↓
  SQLite DB (정부과제_트렌드_창고.db)
               ↓
  g2b_pdf_budget.py  → Playwright로 공고 PDF 다운로드 → 사업금액 추출
               ↓
  g2b_export_json.py -y
    ├─ DB → announcements.json 변환
    ├─ git push → GitHub Actions → GitHub Pages 갱신
    └─ Redmine 새기능 트래커 자동 등록 (5점+ 신규 건)

[클라우드 — 열람 단계 (24시간, PC 불필요)]
  GitHub Pages 대시보드
  https://jscompany909-cpu.github.io/g2b-dashboard/

  Redmine 새기능 트래커
  http://192.168.14.19:3000/projects/g2b_project/issues?tracker_id=2
```

---

## 2. 수집 소스 상세

| 출처 | 방식 | 수집량 | 비고 |
|------|------|--------|------|
| **G2B** (나라장터) | 공공 Open API (`apis.data.go.kr`) | 최대 10,000건 | 용역 입찰공고, 병렬 5 페이지 동시 |
| **IITP** | 내부 REST API 직접 호출 | 최대 600건 | Playwright 불필요, requests + 세션 쿠키 |
| **KISA** | HTML 스크래핑 (BeautifulSoup) | 약 100건 | 공개 API 없음 |
| **NIPA** | HTML 스크래핑 (BeautifulSoup) | 약 100건 | 공개 API 없음 |

> KISA·NIPA는 공개 API가 없어 스크래핑 유지. 두 기관 모두 나라장터에 공고를 올리므로 G2B API에서 일부 중복 수집됨.

---

## 3. AI 채점 모델 — Azure GPT-4o

### 벤치마크 결과 (GPT-4o vs o4-mini)

| 항목 | GPT-4o | o4-mini |
|------|--------|---------|
| 완주 | 20/20건 ✅ | 3/20건 ❌ (API 오류) |
| 일관성 (std ≤ 1) | **100%** | 100% (표본 부족) |
| 평균 std | **0.00** | 0.24 |
| 응답 속도 | **2.8초/건** | 3.7초/건 |

→ **GPT-4o 채택** (안정성·일관성·속도 모두 우세)

### 채점 기준

| 점수 | 판단 |
|------|------|
| 9~10점 | 이노티움 제품 **직접 납품** 가능 (DLP, ECM, N2SF, 백업, 랜섬웨어 등) |
| 7~8점  | 보안 솔루션 구축·도입 관련 |
| 5~6점  | 관련 제품 일부 포함 가능성 |
| 2~4점  | 낮은 관련성 |
| 0~1점  | 무관 / 컨설팅·감사·교육·관제 전용 |

**타겟 키워드 104개**: DLP, ECM, 문서중앙화, 랜섬웨어, N2SF, 제로트러스트, SBOM, 개인정보보호 솔루션 등

---

## 4. 하네스(Harness) 구조

```
g2b_harness.py 실행 흐름:

Phase 0  수집 (G2B + IITP + KISA + NIPA)
    ↓
Round 1~3 반복 (목표 신뢰도 80% 달성 시 조기 종료):
  Phase 1  평가 — 최근 1개월 점수 분포 측정, 불확실 구간(4~7점) 비율 → 신뢰도 산출
  Phase 2  검증 — 불확실 항목 3회 재채점, 표준편차로 일관성 측정
  Phase 3  반영 — 중앙값으로 DB 업데이트 (라운드당 최대 50건)
    ↓
최종 보고 — 라운드별 신뢰도 변화 + 최근 1개월 Top 후보 출력

실제 결과: 신뢰도 96.8% → Round 1 조기 완료
```

---

## 5. PDF 사업금액 자동 추출

### 작동 방식

```
g2b_pdf_budget.py:

Playwright로 G2B 공고 페이지 접근 (봇 감지 우회)
    → 파일첨부 섹션에서 '.pdf' 텍스트 요소 탐지
    → page.expect_download()으로 공고서(변환본).pdf 다운로드
    → pdfplumber로 텍스트 추출
    → 금액 패턴 파싱 (아래 우선순위 순)
    → DB budget 컬럼 업데이트
```

### 지원 금액 패턴

| 패턴 예시 | 비고 |
|-----------|------|
| `[추정가격: 54,545,455원]` | 가장 우선 |
| `추정가격 : 54,545,455원` | |
| `기초금액 금 42,220,000원(부가세 포함)` | |
| `금액 : 60,000,000원` | |
| `금액 : 6,000(천원)` | 천원 단위 자동 변환 |
| `금39,600,000원` | 독립 패턴 |

### 금액 추출 결과 (현재 Top 후보)

| 공고명 | 금액 |
|--------|------|
| 정보시스템 웹서버·백업스토리지 고도화 | **1.4억** |
| 디지털의료제품 전주기 관리시스템 구축 | **4.4억** |
| 2026년 보안솔루션 유지보수 | **1.8억** |
| 과기정통부 N2SF 적용 업무자료 등급분류 | **5,454만원** |
| 자동백업시스템 유지보수 계약 | **4,222만원** |
| 주소정보관리시스템 백업장비(PTL) 도입 | **3,960만원** |
| 수의계약·협상 방식 3건 | - (법적 비공개) |

> 수의계약·협상 방식은 금액이 공개되지 않으므로 추출 불가

---

## 6. 대시보드 구성

**접속 URL**: `https://jscompany909-cpu.github.io/g2b-dashboard/`
**GitHub 레포**: `https://github.com/jscompany909-cpu/g2b-dashboard`

| 탭 | 내용 | 필터 |
|----|------|------|
| 🎯 Top 후보 | 5점 이상 | 마감 미경과 건만 (지원 가능 상태) |
| 📋 전체 신규 | 1점 이상 | 최근 30일 |
| ⏰ 마감임박 | 점수 무관 | 14일 내 마감 |
| 📊 통계 | 발주처 TOP10, 카테고리 분포 | - |
| 🔍 Raw | 전체 원본 | 최근 30일 |

**컬럼 구성**: 출처뱃지(G2B/KISA/NIPA/IITP) · 발주기관 · 공고명 · **금액** · 공고일 · 마감일 · AI점수 · 카테고리 · AI분석

---

## 7. Redmine 연동

| 트래커 | 등록 조건 | 우선순위 |
|--------|-----------|---------|
| **새기능** (tracker_id=2) | 5점+, 미등록, 마감 미경과 | 점수+마감일 자동 계산 |
| **리서치** (tracker_id=8) | 수동 (본 문서 등) | 보통 |

**우선순위 자동 계산 로직:**
- 마감 3일 이내 + 7점 이상 → **즉시**
- 마감 7일 이내 + 7점 이상 → **긴급**
- 9점 이상 → **높음**
- 7점 이상 → **보통**
- 그 외 → **낮음**

---

## 8. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `g2b_harness.py` | **메인 실행** — 수집·평가·검증·재채점 오케스트레이션 |
| `g2b_bid_collector.py` | G2B API 수집 + Azure GPT-4o 채점 |
| `scrapers.py` | KISA·NIPA·IITP 수집 (requests/스크래핑) |
| `g2b_pdf_budget.py` | Playwright로 공고 PDF 다운로드 → 사업금액 추출 |
| `g2b_export_json.py` | DB→JSON 변환 + GitHub push + Redmine 새기능 등록 |
| `g2b_redmine_memo.py` | Redmine 리서치 메모 등록 (이 파일) |
| `g2b_model_benchmark.py` | GPT-4o vs o4-mini 채점 정합성 비교 |
| `g2b-dashboard-repo/` | GitHub Pages 정적 대시보드 |
| `정부과제_트렌드_창고.db` | SQLite DB (수집 데이터 누적) |
| `.env` | API 키 모음 (G2B, Azure OpenAI, Redmine 등) |

---

## 9. 데이터 갱신 방법 (PC 켤 때)

```powershell
cd C:\\Users\\J.S.Bae\\Documents\\VScode

# Step 1: 수집 + GPT-4o 채점 + 신뢰도 검증 (약 5~10분)
python g2b_harness.py

# Step 2: PDF에서 사업금액 추출 (7점+ 대상, 약 3~5분)
python g2b_pdf_budget.py

# Step 3: GitHub Pages 갱신 + Redmine 새기능 자동 등록
python g2b_export_json.py -y
```

**옵션:**
```powershell
python g2b_harness.py --no-collect   # 수집 건너뛰고 재채점만
python g2b_harness.py 5              # 최대 5라운드 검증
```

---

## 10. PC 필요 여부

| 기능 | PC 필요 | 이유 |
|------|---------|------|
| 대시보드 보기 | ❌ | GitHub Pages 24시간 서빙 |
| Redmine 보기 | ❌ | 사내 서버 상시 가동 |
| 새 데이터 수집 | ✅ | G2B/IITP API 호출 |
| AI 채점 | ✅ | Azure GPT-4o API 호출 |
| PDF 금액 추출 | ✅ | Playwright 실행 |
| 데이터 업로드 | ✅ | git push |

---

## 11. 환경 설정 (.env)

```
G2B_API_KEY=...                          # 나라장터 공공 API
AZURE_OPENAI_ENDPOINT=https://inno-ecm-test01.openai.azure.com
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2025-01-01-preview
REDMINE_URL=http://192.168.14.19:3000
REDMINE_USER=admin
REDMINE_PASS=...
REDMINE_PROJECT=g2b_project
```

---

## 12. 향후 업그레이드 방향

PC 없이 완전 자동화 가능:
- **GitHub Actions 스케줄** → 매일 자동 수집·채점 (`cron: '0 9 * * 1-5'`)
- Redmine은 사내망이므로 GitHub Actions에서 접근 불가 → 로컬 PC 접속 시에만 Redmine 등록
- 예상 비용: GPT-4o 채점 70건 기준 월 **$0.5 미만**
"""


def main():
    body = {
        "issue": {
            "project_id":  REDMINE_PROJECT,
            "tracker_id":  TRACKER_ID,
            "subject":     "[설계서 v2] 이노티움 G2B 입찰 인텔리전스 시스템 — 전체 설계",
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
