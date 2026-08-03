"""
g2b_redmine_memo.py — Redmine 리서치 트래커에 설계서 등록
"""
import html
import os
import requests

# CHANGE 1: Redmine config from environment variables
REDMINE_URL     = os.environ.get("REDMINE_URL", "")
REDMINE_USER    = os.environ.get("REDMINE_USER", "")
REDMINE_PASS    = os.environ.get("REDMINE_PASS", "")
REDMINE_AUTH    = (REDMINE_USER, REDMINE_PASS)
REDMINE_PROJECT = "g2b_project"
TRACKER_ID      = 8

# CHANGE 2: dynamic repo directory — no hardcoded Windows path
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))

CONTENT = """## 이노티움 G2B 입찰 인텔리전스 시스템 — 전체 설계서 v4

> 나라장터·KISA·NIPA·IITP 공고를 자동 수집·채점하고, Cloudflare Workers가 매일 정각 09:30에 이메일을 발송하는 완전 자동화 파이프라인.

---

## 1. 전체 아키텍처

```
[GitHub Actions — 평일 08:00 KST] collect.yml
  나라장터 G2B API  (최대 10,000건)
  IITP REST API    (최대   600건)  →  g2b_harness.py  →  SQLite DB
  KISA 스크래핑    (약    100건)       수집·채점·검증
  NIPA 스크래핑    (약    100건)
           ↓
  g2b_pdf_budget.py  →  PDF/HWPX 사업금액 추출
           ↓
  g2b_export_json.py --ci  →  JSON push  →  GitHub Pages 갱신

[Cloudflare Workers — 평일 09:30 KST 정각]
  cron: "30 0 * * 1-5" (UTC)
  worker.js  →  GitHub API workflow_dispatch  →  notify.yml 즉시 실행
           ↓
  g2b_email_notify.py  →  HTML 이메일  →  jsbae@innotium.com

[클라우드 — 24시간 열람]
  GitHub Pages 대시보드
  https://jscompany909-cpu.github.io/g2b-dashboard/
```

---

## 2. 타이밍 구조 (GitHub Actions 지연 문제 해결)

### 문제
GitHub Actions 무료 플랜은 스케줄 실행이 **1~3시간 지연**되는 경우 있음.
이전 방식(wait 루프)으로도 runner 시작 자체가 늦으면 해결 불가.

### 해결 — Cloudflare Workers Cron Trigger
- Cloudflare Workers는 **초 단위 정확도** (서버리스 엣지 네트워크)
- Worker가 정각 09:30 KST에 GitHub API를 직접 호출
- `workflow_dispatch` 트리거는 큐 없이 **즉시 실행** (scheduled와 다름)
- 결과: GitHub Actions 실행 시작 1~2분 + 이메일 발송 → **09:31~09:32 도착 보장**

```
09:30:00 KST  Cloudflare Worker cron 실행
09:30:01      GitHub API workflow_dispatch 호출
09:30:05      notify.yml 즉시 시작
09:30:30      이메일 발송 완료
09:30:35      jsbae@innotium.com 수신
```

---

## 3. 워크플로우 파일 구조

| 파일 | 역할 | 스케줄 |
|------|------|--------|
| `.github/workflows/collect.yml` | 수집·채점·PDF금액·JSON push | 평일 08:00 KST |
| `.github/workflows/notify.yml` | 이메일 발송 | Cloudflare가 트리거 |
| `.github/workflows/deploy-cf-worker.yml` | Cloudflare Worker 자동 배포 | cloudflare-cron/ 변경 시 |
| `.github/workflows/deploy-pages.yml` | GitHub Pages 배포 | index.html 변경 시 |

---

## 4. Cloudflare Worker 구조

**파일**: `cloudflare-cron/worker.js`, `cloudflare-cron/wrangler.toml`

```javascript
// 평일 09:30 KST (UTC 00:30)에 자동 실행
// cron: "30 0 * * 1-5"

scheduled(event, env, ctx) {
  POST https://api.github.com/repos/.../actions/workflows/notify.yml/dispatches
  Authorization: Bearer ${env.GITHUB_TOKEN}
}
```

**환경변수 (Cloudflare Secret)**
- `GITHUB_TOKEN`: GitHub PAT — Worker 배포 시 자동 설정됨

**배포 방식**: GitHub Actions `deploy-cf-worker.yml`이 `cloudflare-cron/` 변경 감지 시 자동 배포
- 필요 GitHub Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`

---

## 5. 수집 소스

| 출처 | 방식 | 수집량 |
|------|------|--------|
| **G2B** (나라장터) | 공공 Open API | 최대 10,000건 |
| **IITP** | REST API 직접 호출 (requests, Playwright 불필요) | 최대 600건 |
| **KISA** | HTML 스크래핑 | 약 100건 |
| **NIPA** | HTML 스크래핑 | 약 100건 |

---

## 6. AI 채점 — Azure GPT-4o

**벤치마크**: GPT-4o(채택) vs o4-mini(탈락 — 3/20건 완주)

| 점수 | 기준 |
|------|------|
| 9~10점 | 직접 납품 가능 (DLP, ECM, N2SF, 백업, 랜섬웨어 등) |
| 7~8점  | 보안 솔루션 구축·도입 관련 |
| 5~6점  | 관련 제품 일부 포함 |
| 0~1점  | 컨설팅·감사·교육·관제 전용 → 반드시 0점 처리 |

---

## 7. 하네스 신뢰도 구조

```
Round 1~3 반복 (신뢰도 80% 달성 시 조기 종료):
  Phase 1  평가  — 불확실 구간(4~7점) 비율 → 신뢰도
  Phase 2  검증  — 3회 재채점, 표준편차 측정
  Phase 3  반영  — 중앙값으로 DB 업데이트
→ 실제 결과: 신뢰도 96.8%, Round 1 조기 완료
```

---

## 8. PDF/HWPX 사업금액 자동 추출

**대상**: 7점 이상, budget=0 항목 (G2B + KISA + NIPA + IITP 모두 포함)

```
Playwright → 공고 페이지 접근 (봇 감지 우회)
  → .pdf → .hwpx → .hwp 우선순위로 첨부파일 탐색
  → expect_download() 다운로드 캡처
  → PDF: pdfplumber / HWPX: ZIP+XML 파싱
  → 금액 패턴 매칭 → DB budget 저장
```

지원 패턴: `[추정가격: X원]`, `기초금액 금 X원`, `금액 : X(천원)`, `금X원` 등

---

## 9. 대시보드 구성

**URL**: https://jscompany909-cpu.github.io/g2b-dashboard/

| 탭 | 내용 | 필터 |
|----|------|------|
| 🎯 Top 후보 | 5점+ | 마감 미경과 건만 |
| 📋 전체 신규 | 1점+ | 최근 30일 |
| ⏰ 마감임박 | 점수 무관 | 14일 내 마감 |
| 📊 통계 | 발주처 TOP10 / 카테고리 분포 / **월별 공고 추이** | - |
| 🔍 Raw | 전체 원본 | 최근 30일 |
| 🗑️ 숨김 | 숨긴 항목 | localStorage |

**첫 화면 (KPI 아래)**: 💡 시사점 & 액션아이템 자동 생성
- 이번 주 핵심 카테고리 / 7일 내 마감 건수 / 주목할 발주처 / 총 예산 / DB 누적

**컬럼**: 출처뱃지 · 발주기관 · 공고명 · **금액** · 공고일 · 마감일 · 점수 · 카테고리 · AI분석

---

## 10. 이메일 알림

| 항목 | 값 |
|------|-----|
| 발신자 | **G2B 알리미** (한글 RFC 2047 인코딩) |
| 발신 계정 | jscompany909@gmail.com |
| 수신 | jsbae@innotium.com |
| 발송 시각 | 평일 09:30 KST 정각 (Cloudflare 보장) |

**이메일 포함 내용**
- KPI 요약 (이번 주 신규 / Top 후보 / 마감임박 / DB 누적)
- Top 후보 테이블 (공고명 클릭 링크 · 점수 · 금액 · 마감일)
- 마감임박 테이블
- 대시보드 열기 버튼

---

## 11. GitHub Secrets 목록

| Secret | 용도 |
|--------|------|
| `G2B_API_KEY` | 나라장터 API |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI |
| `AZURE_OPENAI_KEY` | GPT-4o API 키 |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | API 버전 |
| `PERSONAL_ACCESS_TOKEN` | GitHub 레포 push 권한 |
| `GMAIL_USER` | 발신 Gmail |
| `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 |
| `CLOUDFLARE_API_TOKEN` | Cloudflare Worker 배포 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 계정 ID |

---

## 12. PC 필요 여부

| 기능 | PC 필요 |
|------|---------|
| 대시보드 보기 | ❌ GitHub Pages |
| 이메일 수신 | ❌ 완전 자동 |
| 새 데이터 수집 | ❌ GitHub Actions |
| AI 채점 | ❌ Azure API |
| PDF 금액 추출 | ❌ GitHub Actions + Playwright |
| Redmine 등록 | ✅ 사내망 — 로컬 수동 |

---

## 13. 주요 버그 수정 이력

| 버그 | 원인 | 수정 |
|------|------|------|
| G2B 수집 CancelledError | ThreadPoolExecutor cancel 시 CancelledError 미처리 | try/except + done 플래그 추가 |
| 이메일 발신자명 깨짐 | 한글 비ASCII 문자 RFC 인코딩 누락 | `Header` + `formataddr` 적용 |
| 이메일 정시 미도착 | GitHub Actions 스케줄 지연 (1~3시간) | Cloudflare Workers 정시 트리거로 전환 |
| 마감일 미표시 | 수의계약/협상 방식 `end_date='-'` | "-" → "미정" 표시로 변경 |

---

## 14. 로컬 수동 실행

```powershell
# 레포 디렉터리는 스크립트 위치 기준으로 자동 결정됩니다.

python g2b_harness.py          # 수집 + 채점 + 검증
python g2b_pdf_budget.py       # PDF/HWPX 금액 추출
python g2b_export_json.py -y   # GitHub Pages 갱신 + Redmine 새기능 등록
python g2b_email_notify.py     # 이메일 수동 발송
```

---

## 15. 검증 기간 이후 설정

`collect.yml`, `notify.yml` 에서 아래 줄 삭제 또는 날짜 변경:
```
DEADLINE=20260724
```
"""


def main():
    # CHANGE 3: early exit if Redmine credentials are not configured
    if not REDMINE_URL or not REDMINE_USER:
        print("오류: REDMINE_URL 또는 REDMINE_USER 환경변수가 설정되지 않았습니다.")
        print("실행 전 다음 환경변수를 설정하세요:")
        print("  REDMINE_URL  — 예: http://192.168.14.19:3000")
        print("  REDMINE_USER — Redmine 사용자 이름")
        print("  REDMINE_PASS — Redmine 비밀번호")
        return

    # CHANGE 4: wrap user-data fields with html.escape() before embedding in Redmine body
    subject = html.escape("[설계서 v4] G2B 입찰 인텔리전스 — Cloudflare Workers 정시 자동화 완성")
    description = html.escape(CONTENT)

    body = {
        "issue": {
            "project_id":  REDMINE_PROJECT,
            "tracker_id":  TRACKER_ID,
            "subject":     subject,
            "description": description,
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
