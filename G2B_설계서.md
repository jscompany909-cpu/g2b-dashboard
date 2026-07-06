# 이노티움 G2B 입찰 인텔리전스 시스템 설계서 v2

> 나라장터·KISA·NIPA·IITP 공고를 자동 수집하고, Azure GPT-4o로 채점해  
> GitHub Pages 대시보드와 Redmine에 자동 등록하는 파이프라인

---

## 실행 명령어 (3단계)

```powershell
cd "C:\Users\J.S.Bae\Documents\VScode"

# 1. 수집 + GPT-4o 채점 + 신뢰도 검증 (~5~10분)
python g2b_harness.py

# 2. PDF에서 사업금액 추출 (7점+ 대상, ~3~5분)
python g2b_pdf_budget.py

# 3. GitHub Pages 갱신 + Redmine 새기능 자동 등록
python g2b_export_json.py -y
```

**대시보드:** https://jscompany909-cpu.github.io/g2b-dashboard/  
**Redmine 새기능:** http://192.168.14.19:3000/projects/g2b_project/issues?tracker_id=2

---

## 전체 아키텍처

```
[PC — 수집·채점]
  나라장터 G2B API  (최대 10,000건)
  IITP REST API    (최대  600건)    →  g2b_harness.py  →  SQLite DB
  KISA 스크래핑    (약    100건)       수집 → 키워드 필터
  NIPA 스크래핑    (약    100건)       → GPT-4o 채점
       ↓
  g2b_pdf_budget.py  →  Playwright로 공고 PDF 다운로드 → 사업금액 추출
       ↓
  g2b_export_json.py -y
    ├─ DB → announcements.json
    ├─ git push → GitHub Actions → GitHub Pages 갱신
    └─ Redmine 새기능 트래커 자동 등록 (5점+ 신규 건)

[클라우드 — 열람 (24시간, PC 불필요)]
  GitHub Pages:  https://jscompany909-cpu.github.io/g2b-dashboard/
  Redmine:       http://192.168.14.19:3000/projects/g2b_project/
```

---

## 핵심 파일

| 파일 | 역할 |
|------|------|
| `g2b_harness.py` | **메인** — 수집·평가·검증·재채점 오케스트레이션 |
| `g2b_bid_collector.py` | G2B API 수집 + Azure GPT-4o 채점 함수 |
| `scrapers.py` | KISA·NIPA·IITP 수집 |
| `g2b_pdf_budget.py` | Playwright로 공고 PDF → 사업금액 추출 |
| `g2b_export_json.py` | DB→JSON + GitHub push + Redmine 등록 |
| `g2b_redmine_memo.py` | Redmine 리서치 메모 등록 |
| `g2b_model_benchmark.py` | GPT-4o vs o4-mini 채점 성능 비교 |
| `g2b-dashboard-repo/` | GitHub Pages 정적 대시보드 소스 |
| `정부과제_트렌드_창고.db` | SQLite 누적 DB |
| `.env` | API 키 모음 |

---

## AI 채점 (Azure GPT-4o)

### 벤치마크 결과

| 항목 | GPT-4o ✅ | o4-mini ❌ |
|------|----------|-----------|
| 완주 | 20/20건 | 3/20건 (API 오류) |
| 일관성 (std ≤ 1) | **100%** | 100% (표본 부족) |
| 평균 표준편차 | **0.00** | 0.24 |
| 응답 속도 | **2.8초/건** | 3.7초/건 |

### 채점 기준

| 점수 | 기준 |
|------|------|
| **9~10점** | 이노티움 제품 직접 납품 가능 (DLP, ECM, N2SF, 백업, 랜섬웨어 등) |
| 7~8점 | 보안 솔루션 구축·도입 관련 |
| 5~6점 | 관련 제품 일부 포함 가능성 |
| 2~4점 | 낮은 관련성 |
| 0~1점 | 무관 / 컨설팅·감사·교육·관제 전용 → 반드시 0점 처리 |

---

## 하네스 구조

```
g2b_harness.py 실행 흐름:

Phase 0  수집 (G2B + IITP + KISA + NIPA)
    ↓
Round 1~3 반복 (신뢰도 80% 달성 시 조기 종료):
  Phase 1  평가  — 점수 분포, 불확실 구간(4~7점) 비율 → 신뢰도
  Phase 2  검증  — 불확실 항목 3회 재채점, 표준편차 측정
  Phase 3  반영  — 중앙값으로 DB 업데이트
    ↓
최종 보고: 라운드별 신뢰도 변화 + Top 후보 목록

→ 실제 결과: 신뢰도 96.8%, Round 1 조기 완료
```

---

## PDF 사업금액 추출

### 동작 방식

```
Playwright로 G2B 공고 페이지 접근 (봇 감지 우회)
 → 파일첨부 섹션에서 '.pdf' 텍스트 요소 탐지
 → page.expect_download() 으로 공고서(변환본).pdf 다운로드
 → pdfplumber 텍스트 추출
 → 금액 패턴 파싱 → DB budget 컬럼 저장
```

### 지원 패턴

```
[추정가격: 54,545,455원]       → 가장 우선
추정가격 : 54,545,455원
기초금액 금 42,220,000원(부가세 포함)
금액 : 60,000,000원
금액 : 6,000(천원)             → 천원 단위 자동 변환
금39,600,000원                 → 독립 패턴
```

### 추출 결과 (현재 Top 후보)

| 공고명 | 금액 |
|--------|------|
| 정보시스템 웹서버·백업스토리지 고도화 | 1.4억 |
| 디지털의료제품 전주기 관리시스템 구축 | 4.4억 |
| 2026년 보안솔루션 유지보수 | 1.8억 |
| 과기정통부 N2SF 적용 업무자료 등급분류 | 5,454만원 |
| 자동백업시스템 유지보수 계약 | 4,222만원 |
| 주소정보관리시스템 백업장비(PTL) 도입 | 3,960만원 |
| 수의계약·협상 방식 3건 | - (법적 비공개) |

---

## 대시보드 탭 구성

| 탭 | 내용 | 필터 |
|----|------|------|
| 🎯 Top 후보 | 5점+ | **마감 미경과 건만** (지원 가능 상태) |
| 📋 전체 신규 | 1점+ | 최근 30일 |
| ⏰ 마감임박 | 점수 무관 | 14일 내 마감 |
| 📊 통계 | 발주처 TOP10, 카테고리 분포 | - |
| 🔍 Raw | 전체 원본 | 최근 30일 |
| 🗑️ 숨김 | 숨긴 항목 | localStorage (브라우저 로컬) |

**컬럼:** 출처뱃지 · 발주기관 · 공고명 · **금액** · 공고일 · 마감일 · 점수 · 카테고리 · AI분석

---

## Redmine 연동

| 트래커 | 등록 조건 | 우선순위 로직 |
|--------|-----------|--------------|
| 새기능 (tracker_id=2) | 5점+, 미등록, 마감 미경과 | 마감일 + 점수 자동 계산 |
| 리서치 (tracker_id=8) | 수동 (설계서 등) | 보통 |

우선순위: 마감 3일+7점↑→즉시 / 마감 7일+7점↑→긴급 / 9점→높음 / 7점→보통

---

## PC 필요 여부

| 기능 | PC 필요 |
|------|---------|
| 대시보드 보기 | ❌ (GitHub Pages) |
| Redmine 보기 | ❌ (사내 서버) |
| 새 데이터 수집 | ✅ |
| AI 채점 (Azure GPT-4o) | ✅ |
| PDF 금액 추출 (Playwright) | ✅ |
| GitHub push | ✅ |

---

## .env 키 목록

```
G2B_API_KEY=...
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

## 향후 업그레이드

PC 없이 완전 자동화 (GitHub Actions 스케줄):
- 매일 오전 9시 자동 수집·채점 `cron: '0 0 * * 1-5'` (UTC 기준)
- Redmine은 사내망이라 GitHub Actions에서 접근 불가 → 로컬 실행 시에만 등록
- 예상 비용: GPT-4o 채점 70건 기준 월 **$0.5 미만**
