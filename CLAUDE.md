# CLAUDE.md — 이노티움 입찰 인텔리전스 대시보드

입찰공고 · 나라장터 사전규격 · 바우처/R&D 지원사업을 한 파이프라인으로 수집해
GitHub Pages 대시보드에 올리고, 대시보드 요약 메일을 보낸다.
이 레포 하나로 운영한다 (구 `g2b-monitor` 레포는 2026-11 통합 후 삭제됨).

- 대시보드: https://jscompany909-cpu.github.io/g2b-dashboard/
- **이 레포는 public이다.** 소스·워크플로 로그·`data/*.json`이 공개된다. 개인 연락처, 영업 전략 문서, 자격증명은 커밋하지 않는다.

## 구성

| 파일 | 역할 |
|---|---|
| `.github/workflows/pipeline.yml` | 평일 08:00 KST(cron `0 23 * * 0-4`) 수집 → 배포 → 메일. `TZ=Asia/Seoul` |
| `g2b_harness.py` → `g2b_bid_collector.py`, `scrapers.py` | 입찰공고 수집(나라장터 API + KISA·NIPA·IITP) + Azure OpenAI 채점 → SQLite DB |
| `g2b_pdf_budget.py` | 공고 PDF에서 사업금액 추출 (실패해도 계속) |
| `g2b_export_json.py --ci` | DB → `data/announcements.json` |
| `g2b_prespec.py` | 나라장터 사전규격 수집 → `data/prespec.json` (Playwright) |
| `g2b_voucher.py` | 기업마당·NIPA 지원사업 + 구글 뉴스 → `data/vouchers.json` (표준 라이브러리만) |
| `deploy-pages.yml` | `index.html` + `data/`만 Pages에 배포 (파이프라인이 호출, push 시에도 실행) |
| `g2b_email_notify.py` | 대시보드 요약 메일, `--preview`, `--alert <URL>`(실패 알림) |
| `index.html` | 대시보드. 탭: Top 후보·사전규격·바우처·전체 신규·마감임박·통계·Raw·숨김 |

사전규격·바우처 단계는 `continue-on-error` — 실패해도 입찰 데이터 갱신과 배포는 진행된다.
`g2b_model_benchmark.py`, `g2b_redmine_memo.py`는 파이프라인에서 쓰지 않지만 유지한다.

## 화면·메일 연결 규칙
- 상세 패널 딥링크: `#d=bid-<id>`, `#d=pre-<oderPlanNo>`, `#d=vou-<pblancId|NIPA_번호>` / 탭: `#tab=<name>`
- 메일 속 공고 링크는 **대시보드 상세로** 보낸다. 원문 사이트 이동은 대시보드 상세 패널에서 한다.
- 나라장터 사전규격은 개별 공고 직링크가 없다 → 원문 버튼은 사전규격 목록을 열고, 사업명 복사 버튼으로 안내한다.
- 공고 텍스트는 반드시 `esc()`로 이스케이프, 링크는 `safeUrl()`로 http(s)만 허용.

## 나라장터 사전규격 접근 방식 (변경 금지 — 반복 디버깅으로 확정)
`g2b_prespec.py`에 적용되어 있다.
1. 비로그인 공개 진입 URL `https://www.g2b.go.kr/link/PRCA001_04/single/?srch=0001&flag=cnrtSl` 을 Playwright로 열어 WebSquare5 세션을 굽는다. `:8341` 포트 등 다른 경로는 SSO 리다이렉트되거나 타임아웃된다.
2. 목록 API `POST /pr/prc/prca/OderReq/selectOderReqList.do` 는 **브라우저 컨텍스트 안에서 `page.evaluate(JS, [args])` 인자 전달 방식 fetch**로만 호출한다.
   - 커스텀 헤더 `submissionid`, `menu-info`, `usr-id` 누락 시 HTTP 403
   - JS 문자열에 JSON을 f-string으로 끼워 넣으면 "Failed to fetch" → 인자 전달 방식 유지
   - Python `requests` + 쿠키 복제는 세션에 따라 403 → 사용하지 않음
3. 세션 초기화는 실측 15~47초. `GOTO_TIMEOUT_MS=90000`, 재시도 3회를 줄이지 말 것.
4. 날짜는 `datetime.now(KST)` 기준 (러너는 UTC).
5. 응답 핵심 필드: `oderPlanNo, bizNm, oderInstUntyGrpNm, bgtSumAmt, prcsYmd, oderPlanPgstNm, prcmBsneSeNm, stdCtrtMthdNm`. 의견마감일은 API에 없어 "등록일+7일 추정"으로 표기한다.
6. 나라장터 구조가 실제로 바뀐 경우에만 `page.on("response")`로 네트워크를 인터셉트해 새 헤더·페이로드를 확인하고 반영한다.

## 나라장터 입찰공고 API (data.go.kr `BidPublicInfoService`)
- `inqryBgnDt`/`inqryEndDt`는 **`YYYYMMDDHHMM` 12자리**, 조회 구간은 **7일 단위로 분할**한다. 30일을 한 번에·8자리로 보내면 `resultCode=07 입력범위값 초과`로 0건이 된다.
- 오류 응답은 `_fetch_page`의 `_diag`가 로그에 남긴다 (인증키 마스킹). public 레포라 로그에 키·URL 전체를 찍지 않는다.

## 바우처 수집 (`g2b_voucher.py`)
- 기업마당 접수중 목록 `selectSIIA200View.do?rows=15&cpage=N` 전체(약 90페이지)를 훑어 로컬 필터. `keyword` 파라미터는 HTTP 500이라 쓰지 않는다. 기업마당 '분야' 값은 점수에 넣지 않는다.
- NIPA 사업공고 `/home/2-2` 3페이지, 구글 뉴스 RSS(동향, 참고용).
- 구글 뉴스는 러너 IP에서 HTTP 503이 잦다: 검색어 간 1.5초, 재시도 5·10·15초. 뉴스 0건 + 뉴스 오류면 직전 `vouchers.json`의 뉴스를 유지하고 `news_stale: true`로 표시한다.

## 키워드 조정 위치 (변경은 사용자 요청이 있을 때만)
- 입찰공고 필터: `g2b_bid_collector.py` `INNOTIUM_KEYWORDS`, 채점 기준은 같은 파일의 GPT 프롬프트
- 사전규격: `g2b_prespec.py` `KEYWORDS`(검색어) / `TIER1`(×3) / `TIER2`(×1) / `EXCLUDE`
- 바우처: `g2b_voucher.py` `TIER1`(×3) / `TIER2`(×1) / `AGENCY_BONUS` / `EXCLUDE` / `MIN_SCORE` — **바우처 키워드 원본은 이 파일 하나**

## Secrets / Variables
- Secrets: `G2B_API_KEY`, `AZURE_OPENAI_*`(4종), `SMTP_USER`, `SMTP_PASS`, `REPORT_TO`
- Variables: `SMTP_HOST`(다우오피스 `smtp.daouoffice.com`), `SMTP_PORT`(465), `MAIL_WEEKDAYS`(미설정 시 `1`=월요일만, 예: `1,2,3,4,5`)
- 회사 메일은 Gmail이 아니라 다우오피스다. SMTP는 465/SSL만 응답한다.
- **비밀번호·키 값은 대화창에서 받거나 출력·파일 저장하지 않는다.** 사용자가 본인 터미널에서 `gh secret set <NAME> -R jscompany909-cpu/g2b-dashboard` 로 직접 입력하게 안내한다. 다른 레포의 같은 이름 시크릿과 헷갈리지 않도록 `-R`을 명시한다.
- 구 시크릿(`GMAIL_*`, `NOTIFY_TO`, `CLOUDFLARE_*`, `PERSONAL_ACCESS_TOKEN`, `REDMINE_*`)은 현재 파이프라인에서 쓰지 않는다.

## 실행·검증
```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt tzdata aiosmtpd
python -m playwright install chromium
python g2b_prespec.py --days 7          # 사전규격만 (나라장터 실접속 ~20초)
python g2b_voucher.py                   # 바우처만 (기업마당 ~1분)
python g2b_email_notify.py --preview    # output/email_preview.html
python -m http.server 8765              # 대시보드 로컬 확인 → http://localhost:8765/#tab=voucher
```
- 원격 실행: `gh workflow run pipeline.yml -f send_mail=false` (메일 없이) / `-f send_mail=true` (발송). 실제 발송 전에는 수신자를 사용자에게 확인한다.
- 입찰 수집(harness)은 Azure·G2B 키가 필요해 원격에서만 돈다. 한 번에 약 10분.
- 러너는 `ubuntu-24.04` 고정 — `ubuntu-latest`(26.04)는 Playwright 미지원.
- Pages 배포에는 `actions: read` 권한이 필요하다 (없으면 간헐적 `Fetching artifact metadata failed`).
- 봇이 push한 커밋은 다른 워크플로를 트리거하지 않는다 → 배포는 파이프라인 안에서 `deploy-pages.yml`을 직접 호출한다.

## 금지 사항
- 나라장터·기업마당에 과도한 요청 금지: 사전규격 키워드 간 0.3초, 기업마당·NIPA 0.4초 대기 유지. 로컬 반복 실행은 필요한 만큼만.
- CAPTCHA·로그인 우회, 프록시 우회 코드를 작성하지 않는다. 공개 페이지가 막히면 멈추고 보고한다.
- `data/*.json` 스키마를 바꾸면 `index.html`과 `g2b_email_notify.py`를 함께 고친다.

## 알려진 미정리 항목
- Cloudflare Worker `g2b-cron`(`cloudflare-cron/`)은 삭제된 `notify.yml`을 호출해 실패한다. Cloudflare 대시보드에서 끄거나, 정시 실행이 필요하면 `pipeline.yml`로 연결한다 (GitHub cron은 최대 수 시간 지연될 수 있음).
- `G2B_설계서.md`는 통합 전 설계 문서다.
