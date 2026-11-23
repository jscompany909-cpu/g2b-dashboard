"""
g2b_prespec.py — 나라장터 사전규격공개(srchTy=0002) 수집 → data/prespec.json

g2b-monitor(사전규격 주간 모니터링 v2)의 수집 로직을 그대로 이식했다.
나라장터 접근 방식(공개 진입 URL 세션 + 브라우저 내 fetch, 커스텀 헤더,
타임아웃 90s·재시도 3회, KST 기준 날짜, 키워드 간 0.3초 대기)은 변경 금지.

실행:
  python g2b_prespec.py            # 최근 30일
  python g2b_prespec.py --days 7
"""
from __future__ import annotations
import re, json, time, argparse, logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, asdict
from pathlib import Path
from playwright.sync_api import sync_playwright

KST = ZoneInfo("Asia/Seoul")

SESSION_URL = "https://www.g2b.go.kr/link/PRCA001_04/single/?srch=0001&flag=cnrtSl"
LIST_API    = "https://www.g2b.go.kr/pr/prc/prca/OderReq/selectOderReqList.do"
PAGE_SIZE   = 100
OUT_FILE    = Path(__file__).resolve().parent / "data" / "prespec.json"

GOTO_TIMEOUT_MS  = 90_000
SETTLE_WAIT_MS   = 10_000
SESSION_RETRIES  = 3

# ── 검색어 / 스코어링 ─────────────────────────────────────────────────────────
KEYWORDS = [
    "제로트러스트", "Zero Trust",
    "문서중앙화", "문서보안", "DLP",
    "등급분류", "망분리", "보안관제",
    "랜섬웨어", "정보유출",
    "클라우드 보안", "AI 보안",
    "보안시스템", "정보보안",
]
TIER1 = [
    "제로트러스트", "Zero Trust", "ZTA", "ZTNA",
    "문서중앙화", "문서관리시스템", "ECM",
    "DLP", "정보유출방지", "내부정보유출",
    "등급분류", "보안등급", "CSO",
    "망분리", "N2SF", "KCMVP",
    "랜섬웨어", "문서보안",
]
TIER2 = [
    "보안관제", "보안시스템", "보안솔루션",
    "클라우드 보안", "AI 보안",
    "생성형 AI", "AI 기반",
    "정보보안", "사이버보안",
    "SaaS", "스마트플랫폼",
]
EXCLUDE = [
    "ECMO", "심폐", "의료기기",
    "영상물등급", "게임물등급", "등급분류교육", "영상물등급위원회",
    "발전제어망",
    "개인정보보호", "개인정보 보호", "ISMS",
    "인재양성", "인력양성", "훈련장", "배상책임보험",
    "체력단련", "급식", "수학여행",
    "퓨즈", "오링", "계전기", "수리부속",
    "조류인플루엔자", "고병원성",
    "CCTV", "청소로봇",
    # v2: 물리보안 노이즈
    "경비", "경호", "보안관)", "보안요원", "시설경비",
]

JS_FETCH = """
async ([url, kw, pg, s, e, sz]) => {
  const h = {"Content-Type":"application/json;charset=UTF-8","Accept":"application/json",
    "submissionid":"mf_wfm_container_smSearchOderReqLstList",
    "menu-info":'{"menuNo":"13713","menuCangVal":"PRCA001_04","bsneClsfCd":"%EC%97%85130025","scrnNo":"00963"}',
    "usr-id":"null"};
  const b = JSON.stringify({"dlOderReqSrchM":{"srchTy":"0002","bizNm":kw,"tkcgSe":"0000",
    "prgrsBgngYmd":s,"prgrsEndYmd":e,"currentPage":pg,"recordCountPerPage":String(sz),
    "srchTyNm":"","picNm":"","stepCd":"","prssCd":"","oderInstUntyGrpNo":"","oderInstUntyGrpNm":"",
    "pbancInstUntyGrpNo":"","pbancInstUntyGrpNm":"","instSearchRangeYn":"","pbancSearchRangeYn":"",
    "preSrchTy":"","oderPlanPgstCd":"","myPrnmYn":"","dtlsPrnmNo":"","dtlsPrnm":"","itemClsfNo":"",
    "itemCfnm":"","itemIdnfNm":"","hskCd":"","hskNm":"","prcmBsneSeNm":""}});
  try { const r = await fetch(url,{method:"POST",headers:h,credentials:"include",body:b});
        return {ok:true,status:r.status,body:await r.text()}; }
  catch(x){ return {ok:false,error:x.toString()}; }
}"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("g2b")


@dataclass
class Notice:
    biz_no: str; title: str; org: str; task: str
    budget_raw: int; budget: str; contract: str
    reg_date: str; deadline: str; status: str
    t1: list; t2: list; score: int; url: str


def clean(s):
    s = re.sub(r"&#40;", "(", s); s = re.sub(r"&#41;", ")", s)
    return re.sub(r"<[^>]+>", "", re.sub(r"&#\d+;", "", s)).strip()

def fmt_date(ymd):
    ymd = str(ymd or "").strip()
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}" if len(ymd) == 8 and ymd.isdigit() else ymd

def fmt_budget(amt):
    if amt <= 0: return "미기재"
    if amt >= 1e8: return f"{amt/1e8:.1f}억"
    if amt >= 1e6: return f"{amt/1e6:.0f}백만"
    return f"{amt:,}원"

def score_item(title):
    t = title.lower()
    if any(x.lower() in t for x in EXCLUDE):
        return [], [], 0, True
    t1 = [k for k in TIER1 if k.lower() in t]
    t2 = [k for k in TIER2 if k.lower() in t]
    return t1, t2, len(t1) * 3 + len(t2), False


# ── 세션 확보 (재시도) ────────────────────────────────────────────────────────
def open_session(p):
    last_err = None
    for attempt in range(1, SESSION_RETRIES + 1):
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"])
        try:
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0",
                locale="ko-KR", timezone_id="Asia/Seoul",
                ignore_https_errors=True, viewport={"width": 1440, "height": 900})
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page = ctx.new_page()
            t0 = time.time()
            page.goto(SESSION_URL, wait_until="networkidle", timeout=GOTO_TIMEOUT_MS)
            page.wait_for_timeout(SETTLE_WAIT_MS)
            if "sso.g2b.go.kr" in page.url:
                raise RuntimeError("SSO 로그인 페이지로 리다이렉트됨 (공개 경로 변경 가능성)")
            log.info(f"세션 확보 (시도 {attempt}, {time.time()-t0:.1f}s)")
            return browser, page
        except Exception as e:
            last_err = e
            log.warning(f"세션 시도 {attempt} 실패: {e}")
            browser.close()
            time.sleep(10 * attempt)
    raise RuntimeError(f"세션 확보 {SESSION_RETRIES}회 실패: {last_err}")


# ── 수집 ─────────────────────────────────────────────────────────────────────
def collect(days: int):
    end_dt = datetime.now(KST)
    start_dt = end_dt - timedelta(days=days)
    s, e = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    log.info(f"수집 기간(KST): {s} ~ {e}")

    notices, seen, errors = [], set(), []
    with sync_playwright() as p:
        browser, page = open_session(p)
        try:
            for kw in KEYWORDS:
                pg, n_new = 1, 0
                while True:
                    r = page.evaluate(JS_FETCH, [LIST_API, kw, pg, s, e, PAGE_SIZE])
                    if not r.get("ok") or r.get("status") != 200:
                        errors.append(f"{kw}: {r.get('error') or 'HTTP ' + str(r.get('status'))}")
                        break
                    try:
                        items = json.loads(r["body"]).get("dlOderReqL", [])
                    except json.JSONDecodeError:
                        errors.append(f"{kw}: 비JSON 응답")
                        break
                    tot = items[0].get("totCnt", 0) if items else 0
                    for it in items:
                        no = it.get("oderPlanNo", "")
                        if no in seen:
                            continue
                        seen.add(no)
                        title = clean(it.get("bizNm", ""))
                        t1, t2, sc, excl = score_item(title)
                        if excl:
                            continue
                        reg = fmt_date(it.get("prcsYmd", ""))
                        stat = it.get("oderPlanPgstNm", "")
                        try:
                            dl = (datetime.strptime(reg, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d") \
                                 if stat == "게시중" else stat
                        except ValueError:
                            dl = stat
                        bgt = int(it.get("bgtSumAmt", 0) or 0)
                        notices.append(Notice(
                            biz_no=no, title=title,
                            org=clean(it.get("oderInstUntyGrpNm", "")),
                            task=it.get("prcmBsneSeNm", "") or "",
                            budget_raw=bgt, budget=fmt_budget(bgt),
                            contract=it.get("stdCtrtMthdNm", "") or "",
                            reg_date=reg, deadline=dl, status=stat,
                            t1=t1, t2=t2, score=sc,
                            url=f"https://www.g2b.go.kr/link/PRCA001_04/single/?srch=0002&flag=cnrtSl&oderPlanNo={no}"))
                        n_new += 1
                    if not items or items[-1].get("nextRowYn") != "Y" or pg * PAGE_SIZE >= tot:
                        break
                    pg += 1
                    time.sleep(0.3)
                log.info(f"  '{kw}': {n_new}건")
        finally:
            browser.close()

    if len(errors) == len(KEYWORDS):
        raise RuntimeError("모든 키워드 조회 실패 — API 헤더/스키마 변경 가능성\n" + "\n".join(errors))
    matched = sorted([n for n in notices if n.score > 0], key=lambda x: (-x.score, -x.budget_raw))
    log.info(f"수집 완료: 후보 {len(notices)}건 / 관련 {len(matched)}건 / 오류 {len(errors)}건")
    return matched, (start_dt, end_dt), errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    matched, (start_dt, end_dt), errors = collect(args.days)
    items = [asdict(n) for n in matched]
    data = {
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "period": f"{start_dt:%Y-%m-%d} ~ {end_dt:%Y-%m-%d}",
        "deadline_note": "의견마감일은 API 미제공 — 등록일+7일 추정",
        "kpi": {
            "total": len(items),
            "active": sum(1 for n in items if n["status"] == "게시중"),
            "tier1": sum(1 for n in items if n["t1"]),
        },
        "errors": errors,
        "items": items,
    }
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"저장: {OUT_FILE} — 관련 {len(items)}건 / 게시중 {data['kpi']['active']}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
