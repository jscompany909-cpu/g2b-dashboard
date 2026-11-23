"""
g2b_voucher.py — 바우처·R&D 지원사업 수집 → data/vouchers.json

g2b-monitor/src/voucher_monitor.py 의 수집·점수 로직을 그대로 이식했다(표준 라이브러리만 사용).
  1) 기업마당(bizinfo) 접수중 공고 전체 목록 (keyword 파라미터는 HTTP 500 → 전체를 훑어 로컬 필터)
  2) NIPA 사업공고 3페이지
  3) Google 뉴스 RSS 동향 (참고용)

  python g2b_voucher.py            # '신규' 판정·뉴스 기간 7일
  python g2b_voucher.py --days 14
"""
from __future__ import annotations
import re, json, html, time, argparse, logging
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, date
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
OUT_FILE = Path(__file__).resolve().parent / "data" / "vouchers.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

BIZINFO_LIST = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do"
BIZINFO_DETAIL = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId="
BIZINFO_MAX_PAGES = 150      # 접수중 공고 약 1,300건 = 15건 × 90페이지
NIPA_LIST = "https://www.nipa.kr/home/2-2"
NIPA_PAGES = 3
REQ_DELAY = 0.4              # 요청 간 대기(초) — 공공 사이트 부하 배려

# ── 스코어링 ──────────────────────────────────────────────────────────────────
# 제목·사업명·기관명을 합친 문자열에서 부분일치. 대소문자 무시.
TIER1 = [  # ×3  AX 컴플라이언스 직결 + 핵심 바우처
    "컴플라이언스", "AI 신뢰성", "인공지능 신뢰성", "신뢰성 검", "AI 기본법", "인공지능기본법", "인공지능 기본법",
    "영향평가", "AI 안전", "인공지능 안전", "고영향", "규제대응", "규제 대응", "기술규제",
    "해외인증", "해외규격", "AI 인증", "시험인증", "검인증", "EU AI", "AI Act",
    "CBAM", "탄소국경", "디지털제품여권", "DPP", "공급망 실사", "공급망 ESG", "ESG 경영",
    "AI 바우처", "AI바우처", "AI 통합 바우처", "수출바우처", "산업바우처", "혁신바우처",
    "데이터바우처", "데이터 바우처", "클라우드 바우처", "AX 바우처", "보안 바우처",
    "정보보호", "개인정보", "SECaaS",
]
TIER2 = [  # ×1  관련 분야
    "바우처", "AX", "인공지능", "AI", "M.AX", "제조AI", "자율제조", "R&D", "기술개발",
    "실증", "인증", "표준", "컨설팅", "보안", "데이터", "클라우드", "수출", "탄소", "ESG", "SaaS",
]
AGENCY_BONUS = {  # 소관부처·수행기관 가산점
    "산업통상": 2, "산업자원": 2, "국가기술표준원": 2, "KOTRA": 1, "대한무역투자진흥공사": 1,
    "한국산업기술진흥원": 1, "한국산업기술기획평가원": 1, "과학기술정보통신부": 1,
    "정보통신산업진흥원": 1, "한국정보통신기술협회": 2, "한국인터넷진흥원": 1, "중소벤처기업진흥공단": 1,
}
EXCLUDE = [  # 바우처지만 대상이 다른 것
    "농업", "농촌", "농식품", "어업", "어촌", "축산", "산림", "임업", "반려", "문화누리", "관광", "숙박",
    "음식점", "외식", "돌봄", "출산", "육아", "장학", "학업", "청소년", "체육", "스포츠", "공연", "예술인",
    "소상공인 경영", "전통시장", "장기재직", "주거", "의료급여", "통합문화이용권",
    "전시회", "박람회", "한국관", "미스터리쇼퍼",
]
MIN_SCORE = 3


def score_text(text: str, agency: str = ""):
    low = text.lower()
    if any(x.lower() in low for x in EXCLUDE):
        return 0, [], []
    t1 = [k for k in TIER1 if k.lower() in low]
    t2 = [k for k in TIER2 if k.lower() in low and not any(k.lower() in x.lower() for x in t1)]
    # "AI"는 영단어 일부(예: MAIN, DETAIL)와 겹치지 않도록 경계 확인
    if "AI" in t2 and not re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", text):
        t2.remove("AI")
    s = 3 * len(t1) + len(t2)
    # 기관 가산은 '이미 관련 있는' 공고의 순위만 올린다 (키워드 1개 + 기관만으로는 통과 불가)
    if not t1 and s < 2:
        return 0, t1, t2
    bonus = sum(v for k, v in AGENCY_BONUS.items() if k in agency)
    return s + bonus, t1, t2


@dataclass
class Item:
    source: str
    title: str
    url: str
    agency: str = ""        # 소관부처 / 수행기관
    period: str = ""        # 신청기간 원문
    reg_date: str = ""      # YYYY-MM-DD
    end_date: str = ""      # YYYY-MM-DD (신청 마감, 파싱 가능할 때)
    category: str = ""
    score: int = 0
    t1: list = field(default_factory=list)
    t2: list = field(default_factory=list)
    is_new: bool = False


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("voucher")


def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"요청 실패 {url}: {last}")


def strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


DATE_RE = re.compile(r"(20\d{2})[-.](\d{2})[-.](\d{2})")


def last_date(s: str) -> str:
    m = DATE_RE.findall(s or "")
    return "-".join(m[-1]) if m else ""


# ── 1) 기업마당 ───────────────────────────────────────────────────────────────
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
PBLANC_RE = re.compile(r"pblancId=(PBLN_\d+)")


def parse_bizinfo(page_html: str):
    out = []
    for row in ROW_RE.findall(page_html):
        m = PBLANC_RE.search(row)
        if not m:
            continue
        tds = [strip(x) for x in TD_RE.findall(row)]
        # 열: 번호 | 분야 | 공고명 | 신청기간 | 소관부처 | 수행기관 | 등록일 | 조회수
        if len(tds) < 7:
            continue
        out.append(Item(
            source="기업마당", title=tds[2], url=BIZINFO_DETAIL + m.group(1),
            category=tds[1], period=tds[3], agency=f"{tds[4]} · {tds[5]}",
            reg_date=last_date(tds[6]), end_date=last_date(tds[3]),
        ))
    return out


def collect_bizinfo(errors: list):
    items, seen = [], set()
    for pg in range(1, BIZINFO_MAX_PAGES + 1):
        try:
            rows = parse_bizinfo(fetch(f"{BIZINFO_LIST}?rows=15&cpage={pg}"))
        except Exception as e:
            errors.append(f"기업마당 p{pg}: {e}")
            break
        fresh = [r for r in rows if r.url not in seen]
        if not fresh:
            break  # 마지막 페이지 이후에는 같은 페이지가 반복되거나 빈 목록
        for r in fresh:
            seen.add(r.url)
        items += fresh
        time.sleep(REQ_DELAY)
    log.info(f"기업마당: 접수중 공고 {len(items)}건 확인 ({pg}페이지)")
    return items


# ── 2) NIPA ───────────────────────────────────────────────────────────────────
NIPA_ROW_RE = re.compile(
    r'<a href="(/home/2-2/\d+)">(.*?)</a>.*?<span class="box[^"]*">(.*?)</span>(.*?)</tr>', re.S)


def collect_nipa(errors: list):
    items = []
    for pg in range(1, NIPA_PAGES + 1):
        try:
            page = fetch(f"{NIPA_LIST}?curPage={pg}")
        except Exception as e:
            errors.append(f"NIPA p{pg}: {e}")
            break
        for href, title, biz, rest in NIPA_ROW_RE.findall(page):
            period = strip(re.sub(r"신청기간\s*:", "", strip(rest).split("신청기간")[-1] if "신청기간" in rest else ""))
            dates = DATE_RE.findall(strip(rest))
            items.append(Item(
                source="NIPA", title=strip(re.sub(r"<!--.*?-->", "", title, flags=re.S)),
                url="https://www.nipa.kr" + href, agency=f"과학기술정보통신부 · NIPA ({strip(biz)})",
                period=" ~ ".join("-".join(d) for d in dates[:2]) if len(dates) >= 2 else period,
                end_date="-".join(dates[1]) if len(dates) >= 2 else "",
                reg_date="-".join(dates[-1]) if dates else "",
            ))
        time.sleep(REQ_DELAY)
    # 페이지 파라미터가 무시되는 경우 중복 제거
    uniq = {i.url: i for i in items}
    log.info(f"NIPA: 공고 {len(uniq)}건 확인")
    return list(uniq.values())


# ── 3) 뉴스 동향 ──────────────────────────────────────────────────────────────
NEWS_QUERIES = [
    "AI 바우처 2027", "AX 바우처", "AI 컴플라이언스 지원사업", "AI 신뢰성 인증 지원사업",
    "AI 기본법 기업 지원", "수출바우처 2027", "산업통상부 바우처", "혁신바우처 2027",
]


def collect_news(days: int, errors: list):
    cutoff = datetime.now(KST) - timedelta(days=days)
    seen, items = set(), []
    for q in NEWS_QUERIES:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
               + "&hl=ko&gl=KR&ceid=KR:ko")
        try:
            root = ET.fromstring(fetch(url))
        except Exception as e:
            errors.append(f"뉴스 '{q}': {e}")
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            try:
                pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
            except Exception:
                continue
            key = re.sub(r"\s+-\s+[^-]+$", "", title)
            if pub < cutoff or key in seen:
                continue
            seen.add(key)
            src = (it.find("source").text if it.find("source") is not None else "") or ""
            items.append(Item(source="뉴스", title=title, url=link, agency=src,
                              reg_date=f"{pub:%Y-%m-%d}"))
        time.sleep(REQ_DELAY)
    log.info(f"뉴스: {len(items)}건 확인 (최근 {days}일)")
    return items


# ── 판정 ──────────────────────────────────────────────────────────────────────
def rank(items, days: int):
    today = datetime.now(KST).date()
    new_cut = today - timedelta(days=days)
    keep = []
    for it in items:
        # 분야(category: 수출·기술 등)는 기업마당 분류값이라 점수에 넣지 않는다
        it.score, it.t1, it.t2 = score_text(it.title, it.agency)
        if it.score < MIN_SCORE:
            continue
        if it.end_date:
            try:
                if date.fromisoformat(it.end_date) < today:
                    continue  # 마감 지난 공고 제외
            except ValueError:
                pass
        try:
            it.is_new = bool(it.reg_date) and date.fromisoformat(it.reg_date) >= new_cut
        except ValueError:
            it.is_new = False
        keep.append(it)
    keep.sort(key=lambda x: (-x.score, x.end_date or "9999"))
    return keep



def item_id(it: Item) -> str:
    m = PBLANC_RE.search(it.url)
    if m:
        return m.group(1)
    m = re.search(r"/home/2-2/(\d+)", it.url)
    return f"NIPA_{m.group(1)}" if m else re.sub(r"\W", "", it.title)[:40]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="'신규' 판정·뉴스 수집 기간(일)")
    args = ap.parse_args()
    errors: list[str] = []

    biz = collect_bizinfo(errors)
    nipa = collect_nipa(errors)
    if not biz and not nipa:
        raise RuntimeError("기업마당·NIPA 모두 0건 — 구조 변경 또는 접속 차단 의심\n" + "\n".join(errors))
    news = collect_news(args.days, errors)

    notices = rank(biz + nipa, args.days)
    for n in news:
        n.score, n.t1, n.t2 = score_text(n.title)
    news_ranked = sorted((n for n in news if n.score >= MIN_SCORE), key=lambda x: x.reg_date, reverse=True)

    items = [{"id": item_id(n), **asdict(n)} for n in notices]
    data = {
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "new_days": args.days,
        "scanned": {"bizinfo": len(biz), "nipa": len(nipa)},
        "kpi": {
            "hot": sum(1 for n in notices if n.t1),
            "new": sum(1 for n in notices if n.is_new),
            "total": len(notices),
            "news": len(news_ranked),
        },
        "errors": errors,
        "items": items,
        "news": [asdict(n) for n in news_ranked],
    }
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    k = data["kpi"]
    log.info(f"저장: {OUT_FILE} — 관련 {k['total']}건 / 핵심 {k['hot']}건 / 신규 {k['new']}건 / 뉴스 {k['news']}건 / 오류 {len(errors)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
