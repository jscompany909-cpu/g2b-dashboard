"""
g2b_email_notify.py — 주간 수집 결과를 이메일로 발송

환경변수 (GitHub Secrets):
  GMAIL_USER        : 발신 Gmail 주소 (예: yourname@gmail.com)
  GMAIL_APP_PASSWORD: Gmail 앱 비밀번호 (16자리, 공백 제거)
  NOTIFY_TO         : 수신 이메일 (기본 jsbae@innotium.com)
"""

import os
import sqlite3
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

DB_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "정부과제_트렌드_창고.db")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
NOTIFY_TO  = os.environ.get("NOTIFY_TO", "jsbae@innotium.com")
DASHBOARD  = "https://jscompany909-cpu.github.io/g2b-dashboard/"


def get_summary():
    if not os.path.exists(DB_PATH):
        return {}

    today      = datetime.date.today().isoformat()
    thirty_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    d14        = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    week_ago   = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def s(sql, p=()):
        return conn.execute(sql, p).fetchone()[0]
    def r(sql, p=()):
        return [dict(x) for x in conn.execute(sql, p).fetchall()]

    nd = "(is_deleted IS NULL OR is_deleted=0)"

    data = {
        "new_this_week": s(f"SELECT COUNT(*) FROM announcements WHERE reg_date >= ? AND {nd}", (week_ago,)),
        "top":  r(f"SELECT title, score, budget, end_date, institution, source, link "
                  f"FROM announcements WHERE score>=5 AND reg_date>=? "
                  f"AND (end_date>=? OR end_date='-') AND {nd} ORDER BY score DESC", (thirty_ago, today)),
        "urgent": r(f"SELECT title, score, end_date, institution "
                    f"FROM announcements WHERE end_date BETWEEN ? AND ? "
                    f"AND end_date!='-' AND score>=1 AND {nd} ORDER BY end_date", (today, d14)),
        "total": s(f"SELECT COUNT(*) FROM announcements WHERE {nd}"),
    }
    conn.close()
    return data


def fmt_budget(b):
    if not b or b == 0:
        return "-"
    if b >= 100_000_000:
        return f"{b/100_000_000:.1f}억"
    if b >= 10_000_000:
        return f"{b//10_000_000}천만"
    return f"{b//1_000:,}천"


def build_html(data):
    today_str = datetime.date.today().strftime("%Y년 %m월 %d일")
    top_rows  = "".join(
        f"""<tr>
          <td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;white-space:nowrap;color:#6b7280;font-size:11px;min-width:35px">{r['source']}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;white-space:nowrap;font-size:12px;min-width:100px">{r['institution'][:18] if r['institution'] else ''}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;font-size:13px">
            <a href="{r['link']}" style="color:#1e3a8a;text-decoration:none;font-weight:500" target="_blank">{r['title'][:50]}</a>
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;text-align:center;white-space:nowrap;min-width:50px">
            <span style="background:{'#e53e3e' if r['score']>=9 else '#dd6b20' if r['score']>=7 else '#38a169'};color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;white-space:nowrap;display:inline-block">{r['score']}점</span>
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;text-align:right;white-space:nowrap;font-weight:700;color:#1e40af;min-width:60px">{fmt_budget(r['budget'])}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;white-space:nowrap;color:#e53e3e;font-weight:600;min-width:90px">{r['end_date']}</td>
        </tr>"""
        for r in data.get("top", [])
    ) or "<tr><td colspan='6' style='padding:20px;text-align:center;color:#9ca3af'>이번 주 Top 후보 없음</td></tr>"

    urgent_rows = "".join(
        f"""<tr>
          <td style="padding:8px 10px;border-bottom:1px solid #fef3c7;white-space:nowrap;font-size:13px;min-width:100px">{r['institution'][:20] if r['institution'] else ''}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #fef3c7;font-size:13px">{r['title'][:55]}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #fef3c7;text-align:center;white-space:nowrap;min-width:50px">
            <span style="background:#718096;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;white-space:nowrap">{r['score']}점</span>
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #fef3c7;white-space:nowrap;color:#e53e3e;font-weight:700;min-width:90px">{r['end_date']}</td>
        </tr>"""
        for r in data.get("urgent", [])
    ) or "<tr><td colspan='4' style='padding:20px;text-align:center;color:#9ca3af'>마감임박 공고 없음</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Malgun Gothic',sans-serif">
<div style="max-width:700px;margin:30px auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1)">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1e1b4b,#1e3a8a);padding:28px 32px;color:white">
    <h1 style="margin:0;font-size:20px">🏆 이노티움 주간 입찰 인텔리전스</h1>
    <p style="margin:6px 0 0;opacity:.7;font-size:13px">{today_str} 기준 | GPT-4o 자동 수집·채점</p>
  </div>

  <!-- KPI -->
  <div style="display:flex;padding:20px 24px;gap:16px;background:#f8fafc;border-bottom:1px solid #e2e8f0">
    <div style="flex:1;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#3b82f6">{data.get('new_this_week', 0)}</div>
      <div style="font-size:11px;color:#6b7280;margin-top:4px">이번 주 신규</div>
    </div>
    <div style="flex:1;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#ef4444">{len(data.get('top', []))}</div>
      <div style="font-size:11px;color:#6b7280;margin-top:4px">Top 후보 (5점+)</div>
    </div>
    <div style="flex:1;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#f59e0b">{len(data.get('urgent', []))}</div>
      <div style="font-size:11px;color:#6b7280;margin-top:4px">14일 내 마감</div>
    </div>
    <div style="flex:1;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#10b981">{data.get('total', 0)}</div>
      <div style="font-size:11px;color:#6b7280;margin-top:4px">DB 누적</div>
    </div>
  </div>

  <!-- Top 후보 -->
  <div style="padding:24px 32px">
    <h2 style="margin:0 0 16px;font-size:15px;color:#1e3a8a">🎯 Top 후보 (5점 이상, 마감 미경과)</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead>
        <tr style="background:#f8fafc">
          <th style="padding:10px 12px;text-align:left;color:#6b7280;font-size:11px;border-bottom:2px solid #e2e8f0">출처</th>
          <th style="padding:10px 12px;text-align:left;color:#6b7280;font-size:11px;border-bottom:2px solid #e2e8f0">발주기관</th>
          <th style="padding:10px 12px;text-align:left;color:#6b7280;font-size:11px;border-bottom:2px solid #e2e8f0">공고명</th>
          <th style="padding:10px 12px;text-align:center;color:#6b7280;font-size:11px;border-bottom:2px solid #e2e8f0">점수</th>
          <th style="padding:10px 12px;text-align:right;color:#6b7280;font-size:11px;border-bottom:2px solid #e2e8f0">금액</th>
          <th style="padding:10px 12px;text-align:left;color:#6b7280;font-size:11px;border-bottom:2px solid #e2e8f0">마감일</th>
        </tr>
      </thead>
      <tbody>{top_rows}</tbody>
    </table>
  </div>

  <!-- 마감임박 -->
  <div style="padding:0 32px 24px">
    <h2 style="margin:0 0 16px;font-size:15px;color:#b45309">⏰ 마감임박 (14일 이내)</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;background:#fffbeb;border-radius:8px;overflow:hidden">
      <thead>
        <tr style="background:#fef3c7">
          <th style="padding:10px 12px;text-align:left;color:#92400e;font-size:11px">발주기관</th>
          <th style="padding:10px 12px;text-align:left;color:#92400e;font-size:11px">공고명</th>
          <th style="padding:10px 12px;text-align:center;color:#92400e;font-size:11px">점수</th>
          <th style="padding:10px 12px;text-align:left;color:#92400e;font-size:11px">마감일</th>
        </tr>
      </thead>
      <tbody>{urgent_rows}</tbody>
    </table>
  </div>

  <!-- 링크 -->
  <div style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;text-align:center">
    <a href="{DASHBOARD}" style="display:inline-block;background:#1e3a8a;color:white;padding:12px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px">📊 대시보드 열기</a>
    <p style="margin:12px 0 0;font-size:11px;color:#9ca3af">자동 발송 · 매주 월요일 오전 9시 · GitHub Actions</p>
  </div>

</div>
</body></html>"""


def send_email(html: str, subject: str):
    if not GMAIL_USER or not GMAIL_PASS:
        print("❌ GMAIL_USER 또는 GMAIL_APP_PASSWORD 환경변수 없음")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"이노티움 G2B 알리미 <{GMAIL_USER}>"
    msg["To"]      = NOTIFY_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.sendmail(GMAIL_USER, NOTIFY_TO, msg.as_string())
        print(f"✅ 이메일 발송 완료 → {NOTIFY_TO}")
        return True
    except Exception as e:
        print(f"❌ 이메일 발송 실패: {e}")
        return False


def main():
    today_str = datetime.date.today().strftime("%Y.%m.%d")
    print("📧 주간 결과 이메일 생성 중...")

    data    = get_summary()
    html    = build_html(data)
    subject = f"[G2B 주간 입찰] {today_str} — Top {len(data.get('top',[]))}건, 마감임박 {len(data.get('urgent',[]))}건"

    send_email(html, subject)


if __name__ == "__main__":
    main()
