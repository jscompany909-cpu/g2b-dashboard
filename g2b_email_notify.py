"""
g2b_email_notify.py — 대시보드 요약 메일 발송

data/announcements.json(입찰공고) + data/prespec.json(사전규격)을 읽어
대시보드 요약 형태의 HTML 메일을 보낸다. 메일 속 모든 공고 링크는
대시보드 상세 화면(#d=...)으로 연결되고, 원문 이동은 대시보드에서 한다.

환경변수 (GitHub Secrets / Variables):
  SMTP_USER, SMTP_PASS : 발신 계정·비밀번호 (다우오피스 등)
  REPORT_TO            : 수신자 (쉼표 구분)
  SMTP_HOST, SMTP_PORT : 기본 smtp.daouoffice.com:465 (SSL)

실행:
  python g2b_email_notify.py            # 발송
  python g2b_email_notify.py --preview  # output/email_preview.html 만 생성
"""
from __future__ import annotations

import html
import json
import os
import smtplib
import sys
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

KST       = ZoneInfo("Asia/Seoul")
ROOT      = Path(__file__).resolve().parent
DASHBOARD = os.environ.get("DASHBOARD_URL", "https://jscompany909-cpu.github.io/g2b-dashboard/")

SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "")
REPORT_TO = os.environ.get("REPORT_TO", "")
SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.daouoffice.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "465")

TOP_N = 5
VOU_TOP_MIN = 5   # Top 후보 바우처: 핵심 키워드(t1) 1개 이상 + 관련도 하한 (index.html TOP_VOU_MIN 과 맞출 것)


def load(name: str) -> dict:
    p = ROOT / "data" / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def link(kind: str, key) -> str:
    return f"{DASHBOARD}#d={kind}-{quote(str(key))}"


def fmt_budget(b) -> str:
    try:
        b = int(b or 0)
    except (TypeError, ValueError):
        return "-"
    if b <= 0:
        return "-"
    if b >= 100_000_000:
        return f"{b/100_000_000:.1f}억"
    if b >= 10_000_000:
        return f"{b//10_000_000}천만"
    return f"{b//1_000:,}천"


def badge(score: int, hi=9, mid=7, lo=5) -> str:
    c = "#e53e3e" if score >= hi else "#dd6b20" if score >= mid else "#38a169" if score >= lo else "#718096"
    return (f'<span style="background:{c};color:#fff;padding:2px 9px;border-radius:10px;'
            f'font-size:11px;font-weight:700;white-space:nowrap">{score}점</span>')


TD = "padding:8px 10px;border-bottom:1px solid #eef2f7;font-size:13px;vertical-align:top"
TH = "padding:8px 10px;text-align:left;color:#6b7280;font-size:11px;border-bottom:2px solid #e2e8f0;background:#f8fafc"


def table(head: list[str], rows: list[str], empty: str) -> str:
    if not rows:
        return f'<p style="margin:0;padding:14px;text-align:center;color:#9ca3af;font-size:13px">{empty}</p>'
    th = "".join(f'<th style="{TH}">{h}</th>' for h in head)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse">'
            f'<thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def title_cell(kind, key, title, sub) -> str:
    return (f'<td style="{TD}"><a href="{esc(link(kind, key))}" style="color:#1e3a8a;'
            f'text-decoration:none;font-weight:600">{esc(title)}</a>'
            f'<div style="color:#6b7280;font-size:11px;margin-top:2px">{esc(sub)}</div></td>')


def section(title: str, color: str, body: str, more_href: str, more_label: str) -> str:
    return f"""
  <tr><td style="padding:22px 28px 4px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-size:15px;font-weight:700;color:{color}">{title}</td>
      <td style="text-align:right;font-size:12px"><a href="{esc(more_href)}" style="color:#3b82f6;text-decoration:none">{more_label} →</a></td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:8px 28px 0">{body}</td></tr>"""


def build(bids: dict, pre: dict, vou: dict | None = None) -> tuple[str, str]:
    vou = vou or {}
    now = datetime.now(KST)
    top = bids.get("top", [])
    urgent = bids.get("deadline", [])
    pre_items = pre.get("items", [])
    pre_active = sorted([n for n in pre_items if n.get("status") == "게시중"],
                        key=lambda n: (-n.get("score", 0), -n.get("budget_raw", 0)))
    new_week = (bids.get("insights") or {}).get("new_this_week", 0)
    # 바우처: 핵심(TIER1) 우선, 신규 우선, 점수·마감 순
    vou_items = vou.get("items", [])
    vou_hot = [n for n in vou_items if n.get("t1")]
    vou_pick = sorted(vou_items, key=lambda n: (not n.get("t1"), not n.get("is_new"),
                                                -n.get("score", 0), n.get("end_date") or "9999"))

    # Top 후보 = 입찰 AI 점수 7점+ (나라장터·KISA·NIPA·IITP) + 바우처 관련도 5점+, 마감 임박 순
    bid_min = int(bids.get("top_score") or 7)
    top_mix = (
        [{"kind": "bid", "key": r["id"], "title": r.get("title"),
          "sub": (r.get("source") or "G2B") + " · " + (r.get("institution") or ""),
          "badge": badge(int(r.get("score") or 0)), "score": int(r.get("score") or 0),
          "budget": fmt_budget(r.get("budget")),
          "end": r.get("end_date") if r.get("end_date") not in (None, "", "-") else ""}
         for r in top if int(r.get("score") or 0) >= bid_min]
        + [{"kind": "vou", "key": n["id"], "title": n.get("title"),
            "sub": f'{n.get("source")} · {n.get("agency")}',
            "badge": badge(int(n.get("score") or 0), hi=9, mid=6, lo=3), "score": int(n.get("score") or 0),
            "budget": "-", "end": n.get("end_date") or ""}
           for n in vou_items if int(n.get("score") or 0) >= VOU_TOP_MIN and n.get("t1")])
    top_mix.sort(key=lambda x: (x["end"] or "9999", -x["score"]))

    kpis = [
        ("#ef4444", len(top_mix), "Top 후보", "top"),
        ("#f59e0b", len(urgent), "14일 내 마감", "deadline"),
        ("#7c3aed", len(pre_active), "사전규격 게시중", "prespec"),
        ("#d97706", len(vou_hot), "바우처 핵심", "voucher"),
        ("#3b82f6", new_week, "이번 주 신규", "new"),
    ]
    kpi_html = "".join(
        f'<td width="20%" style="text-align:center;padding:16px 4px">'
        f'<a href="{esc(DASHBOARD)}#tab={tab}" style="text-decoration:none">'
        f'<div style="font-size:28px;font-weight:700;color:{c}">{v}</div>'
        f'<div style="font-size:11px;color:#6b7280;margin-top:4px">{label}</div></a></td>'
        for c, v, label, tab in kpis)

    top_rows = [
        f'<tr>{title_cell(x["kind"], x["key"], x["title"], ("바우처 · " if x["kind"] == "vou" else "입찰 · ") + x["sub"])}'
        f'<td style="{TD};text-align:center">{x["badge"]}</td>'
        f'<td style="{TD};text-align:right;white-space:nowrap;color:#1e40af;font-weight:700">{esc(x["budget"])}</td>'
        f'<td style="{TD};white-space:nowrap;color:#e53e3e;font-weight:600">{esc(x["end"] or "미정")}</td></tr>'
        for x in top_mix[:TOP_N * 2]]

    pre_rows = [
        f'<tr>{title_cell("pre", n["biz_no"], n.get("title"), n.get("org") or "")}'
        f'<td style="{TD};text-align:center">{badge(int(n.get("score") or 0), hi=6, mid=3, lo=1)}</td>'
        f'<td style="{TD};text-align:right;white-space:nowrap;color:#1e40af;font-weight:700">{esc(n.get("budget"))}</td>'
        f'<td style="{TD};white-space:nowrap;color:#7c3aed;font-weight:600">{esc(n.get("deadline"))}</td></tr>'
        for n in pre_active[:TOP_N * 2]]

    urg_rows = [
        f'<tr>{title_cell("bid", r["id"], r.get("title"), r.get("institution") or "")}'
        f'<td style="{TD};text-align:center">{badge(int(r.get("score") or 0))}</td>'
        f'<td style="{TD};white-space:nowrap;color:#e53e3e;font-weight:700">{esc(r.get("end_date"))}</td></tr>'
        for r in urgent[:TOP_N]]

    def vou_flags(n):
        f = ""
        if n.get("t1"):
            f += ('<span style="background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:6px;'
                  'font-size:10px;font-weight:700;margin-right:4px">핵심</span>')
        if n.get("is_new"):
            f += ('<span style="background:#dcfce7;color:#166534;padding:1px 6px;border-radius:6px;'
                  'font-size:10px;font-weight:700;margin-right:4px">신규</span>')
        return f

    vou_rows = [
        f'<tr><td style="{TD}">{vou_flags(n)}<a href="{esc(link("vou", n["id"]))}" style="color:#1e3a8a;'
        f'text-decoration:none;font-weight:600">{esc(n.get("title"))}</a>'
        f'<div style="color:#6b7280;font-size:11px;margin-top:2px">{esc(n.get("source"))} · {esc(n.get("agency"))}</div></td>'
        f'<td style="{TD};text-align:center">{badge(int(n.get("score") or 0), hi=9, mid=6, lo=3)}</td>'
        f'<td style="{TD};white-space:nowrap;color:#b45309;font-weight:600">{esc(n.get("end_date") or "상시/미정")}</td></tr>'
        for n in vou_pick[:TOP_N]]

    body = "".join([
        section(f"🎯 Top 후보 (입찰 {bid_min}점+ · 바우처 핵심 키워드+{VOU_TOP_MIN}점+, 마감 임박 순)", "#1e3a8a",
                table(["공고명", "점수", "금액", "마감"], top_rows, "Top 후보 없음"),
                f"{DASHBOARD}#tab=top", f"전체 {len(top_mix)}건"),
        section("📝 사전규격 게시중 (의견 제출 가능)", "#6d28d9",
                table(["사업명", "관련도", "예산", "의견마감(추정)"], pre_rows, "게시중 사전규격 없음"),
                f"{DASHBOARD}#tab=prespec", f"전체 {len(pre_active)}건"),
        section("⏰ 마감임박 (14일 이내)", "#b45309",
                table(["공고명", "점수", "마감"], urg_rows, "마감임박 공고 없음"),
                f"{DASHBOARD}#tab=deadline", f"전체 {len(urgent)}건"),
        section("💰 바우처·R&amp;D 지원사업", "#b45309",
                table(["사업명", "관련도", "신청마감"], vou_rows,
                      "바우처 데이터 없음" if not vou else "관련 지원사업 없음"),
                f"{DASHBOARD}#tab=voucher", f"전체 {len(vou_items)}건 (핵심 {len(vou_hot)})"),
    ])

    errors = pre.get("errors") or []
    warn = ""
    if not pre:
        warn = "⚠️ 사전규격 데이터가 없습니다 (수집 실패 가능)."
    elif os.environ.get("PRESPEC_OUTCOME", "success") not in ("success", ""):
        warn = f"⚠️ 이번 실행에서 사전규격 수집이 실패해 {pre.get('updated_at', '이전')} 기준 데이터를 표시합니다."
    elif errors:
        warn = f"⚠️ 사전규격 일부 키워드 조회 실패 {len(errors)}건 — 대시보드 수치가 일부 누락될 수 있습니다."
    warn_html = (f'<tr><td style="padding:12px 28px 0"><div style="background:#fef3c7;color:#92400e;'
                 f'padding:10px 14px;border-radius:6px;font-size:12px">{esc(warn)}</div></td></tr>') if warn else ""

    doc = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f8"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:720px;background:#fff;border-radius:12px;overflow:hidden">
  <tr><td style="background:#1e3a8a;background:linear-gradient(135deg,#1e1b4b,#1e3a8a);padding:24px 28px;color:#fff">
    <div style="font-size:19px;font-weight:700">이노티움 입찰 인텔리전스 요약</div>
    <div style="font-size:12px;opacity:.75;margin-top:6px">{now:%Y년 %m월 %d일} 기준 · 입찰공고(나라장터·KISA·NIPA·IITP) + 나라장터 사전규격 + 바우처·지원사업(기업마당·NIPA)</div>
  </td></tr>
  <tr><td style="background:#f8fafc;border-bottom:1px solid #e2e8f0">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{kpi_html}</tr></table>
  </td></tr>
  {warn_html}
  {body}
  <tr><td style="padding:26px 28px;text-align:center">
    <a href="{esc(DASHBOARD)}" style="display:inline-block;background:#1e3a8a;color:#fff;padding:13px 34px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px">📊 대시보드에서 전체 보기</a>
    <p style="margin:12px 0 0;font-size:11px;color:#9ca3af">대시보드는 매일 08:00(KST)에 갱신되고, 이 요약 메일은 주 1회(월요일) 발송됩니다.<br>공고명을 누르면 대시보드 상세 화면이 열리고, 거기서 원문(나라장터 등)으로 이동할 수 있습니다.<br>
    데이터 기준: 입찰 {esc(bids.get("updated_at", "-"))} · 사전규격 {esc(pre.get("updated_at", "-"))} · 바우처 {esc(vou.get("updated_at", "-"))} · 사전규격 의견마감일은 등록일+7일 추정</p>
  </td></tr>
</table></td></tr></table>
</body></html>"""

    subject = (f"[G2B 주간 요약] {now:%m/%d} — Top {len(top_mix)} · 사전규격 게시중 {len(pre_active)}"
               f" · 마감임박 {len(urgent)} · 바우처 핵심 {len(vou_hot)}")
    return subject, doc


def send(subject: str, doc: str) -> bool:
    to = [x.strip() for x in REPORT_TO.split(",") if x.strip()]
    if not (SMTP_USER and SMTP_PASS and to):
        print("❌ SMTP_USER / SMTP_PASS / REPORT_TO 미설정 — 발송 중단")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = formataddr((str(Header("G2B 알리미", "utf-8")), SMTP_USER))
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(doc, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to, msg.as_string())
        print(f"✅ 메일 발송 완료 → {len(to)}명")
        return True
    except Exception as e:
        print(f"❌ 메일 발송 실패: {type(e).__name__}: {e}")
        return False


def alert(run_url: str) -> int:
    now = datetime.now(KST)
    doc = (f'<div style="font-family:Malgun Gothic,sans-serif;max-width:600px;margin:20px auto;padding:24px;'
           f'border:1px solid #fecaca;border-radius:10px;background:#fff5f5">'
           f'<h2 style="margin:0 0 10px;color:#b91c1c;font-size:17px">⚠️ G2B 통합 파이프라인 실행 실패</h2>'
           f'<p style="font-size:13px;color:#374151">{now:%Y-%m-%d %H:%M} KST 실행이 실패했습니다. '
           f'대시보드가 갱신되지 않았을 수 있습니다.</p>'
           f'<p><a href="{esc(run_url)}" style="color:#1e3a8a;font-weight:700">실행 로그 열기</a> · '
           f'<a href="{esc(DASHBOARD)}" style="color:#1e3a8a">대시보드</a></p></div>')
    return 0 if send(f"[G2B ⚠ 실행 실패] {now:%m/%d} 통합 파이프라인", doc) else 1


def main() -> int:
    if "--alert" in sys.argv:
        return alert(sys.argv[sys.argv.index("--alert") + 1] if len(sys.argv) > sys.argv.index("--alert") + 1 else DASHBOARD)
    bids, pre = load("announcements.json"), load("prespec.json")
    if not bids:
        print("❌ data/announcements.json 없음 — 발송 중단")
        return 1
    subject, doc = build(bids, pre, load("vouchers.json"))
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    (out / "email_preview.html").write_text(doc, encoding="utf-8")
    print(f"📧 제목: {subject}")
    if "--preview" in sys.argv:
        print(f"미리보기 저장: {out / 'email_preview.html'}")
        return 0
    return 0 if send(subject, doc) else 1


if __name__ == "__main__":
    raise SystemExit(main())
