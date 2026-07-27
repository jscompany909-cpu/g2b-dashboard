"""
g2b_export_json.py — DB → GitHub Pages JSON 내보내기 + Redmine 새기능 자동 등록

실행:
  python g2b_export_json.py       # JSON 생성만
  python g2b_export_json.py -y    # JSON 생성 + git push + Redmine 자동 등록
"""

import datetime
import json
import os
import sqlite3
import subprocess
import sys

import requests

DB_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "정부과제_트렌드_창고.db")
OUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT_FILE  = os.path.join(OUT_DIR, "announcements.json")

REDMINE_URL     = "http://192.168.14.19:3000"
REDMINE_AUTH    = ("admin", "11111111")
REDMINE_PROJECT = "g2b_project"
TRACKER_ID      = 2   # 새기능


def _migrate_db():
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(announcements)").fetchall()]
    if "budget" not in cols:
        conn.execute("ALTER TABLE announcements ADD COLUMN budget INTEGER DEFAULT 0")
        conn.commit()
    conn.close()


def build_json() -> dict:
    if not os.path.exists(DB_PATH):
        print("❌  DB 없음 — 먼저 g2b_harness.py 또는 g2b_bid_collector.py 를 실행하세요.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    today      = datetime.date.today().isoformat()
    thirty_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    week_ago   = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    d7         = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    d14        = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    six_mo_ago = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()

    def rows(sql, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def scalar(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    SELECT_COLS = (
        "id, institution, title, reg_date, end_date, score, category_tag, ai_reason, "
        "link, COALESCE(source,'G2B') AS source, COALESCE(budget,0) AS budget"
    )
    NOT_DELETED = "(is_deleted IS NULL OR is_deleted=0)"

    data = {
        "kpi": {
            "new_week": scalar(
                f"SELECT COUNT(*) FROM announcements WHERE reg_date >= ? AND {NOT_DELETED}",
                (thirty_ago,)
            ),
            "top7": scalar(
                f"SELECT COUNT(*) FROM announcements WHERE score >= 5 AND reg_date >= ? "
                f"AND (end_date >= ? OR end_date = '-') AND {NOT_DELETED}",
                (thirty_ago, today)
            ),
            "total": scalar(
                f"SELECT COUNT(*) FROM announcements WHERE {NOT_DELETED}"
            ),
            "deadline": scalar(
                f"SELECT COUNT(*) FROM announcements "
                f"WHERE end_date BETWEEN ? AND ? AND end_date!='-' AND score>=1 AND {NOT_DELETED}",
                (today, d14)
            ),
        },
        "top": rows(
            f"SELECT {SELECT_COLS} FROM announcements "
            f"WHERE score>=5 AND reg_date>=? "
            f"AND (end_date >= ? OR end_date = '-') AND {NOT_DELETED} ORDER BY score DESC",
            (thirty_ago, today)
        ),
        "new": rows(
            f"SELECT {SELECT_COLS} FROM announcements "
            f"WHERE reg_date>=? AND score>=1 AND {NOT_DELETED} ORDER BY score DESC",
            (thirty_ago,)
        ),
        "deadline": rows(
            f"SELECT {SELECT_COLS} FROM announcements "
            f"WHERE end_date BETWEEN ? AND ? AND end_date!='-' AND score>=1 AND {NOT_DELETED} "
            f"ORDER BY end_date",
            (today, d14)
        ),
        "inst_top10": rows(
            f"SELECT institution AS name, COUNT(*) AS cnt FROM announcements "
            f"WHERE {NOT_DELETED} GROUP BY institution ORDER BY cnt DESC LIMIT 10"
        ),
        "cat_dist": rows(
            f"SELECT category_tag AS tag, COUNT(*) AS cnt FROM announcements "
            f"WHERE category_tag NOT IN ('오류','') AND {NOT_DELETED} "
            f"GROUP BY category_tag ORDER BY cnt DESC LIMIT 15"
        ),
        "raw": rows(
            f"SELECT {SELECT_COLS} FROM announcements "
            f"WHERE reg_date>=? AND {NOT_DELETED} ORDER BY reg_date DESC, score DESC",
            (thirty_ago,)
        ),
        # ── 월별 공고 추이 (최근 6개월) ─────────────────────────────────
        "monthly_trend": rows(
            f"SELECT strftime('%Y-%m', reg_date) AS month, "
            f"COUNT(*) AS total, "
            f"SUM(CASE WHEN score>=5 THEN 1 ELSE 0 END) AS core "
            f"FROM announcements WHERE reg_date>=? AND {NOT_DELETED} "
            f"GROUP BY month ORDER BY month",
            (six_mo_ago,)
        ),

        # ── 시사점 & 액션아이템 ────────────────────────────────────────
        "insights": {
            "top_category":       (rows(
                f"SELECT category_tag, COUNT(*) AS cnt FROM announcements "
                f"WHERE score>=5 AND reg_date>=? AND category_tag NOT IN ('오류','') AND {NOT_DELETED} "
                f"GROUP BY category_tag ORDER BY cnt DESC LIMIT 1", (week_ago,)) or [{}]
            )[0].get("category_tag", "없음"),
            "top_category_count": (rows(
                f"SELECT category_tag, COUNT(*) AS cnt FROM announcements "
                f"WHERE score>=5 AND reg_date>=? AND category_tag NOT IN ('오류','') AND {NOT_DELETED} "
                f"GROUP BY category_tag ORDER BY cnt DESC LIMIT 1", (week_ago,)) or [{}]
            )[0].get("cnt", 0),
            "urgent_7day": scalar(
                f"SELECT COUNT(*) FROM announcements "
                f"WHERE end_date BETWEEN ? AND ? AND end_date!='-' AND score>=5 AND {NOT_DELETED}",
                (today, d7)
            ),
            "top_institution": (rows(
                f"SELECT institution, COUNT(*) AS cnt FROM announcements "
                f"WHERE score>=3 AND reg_date>=? AND {NOT_DELETED} "
                f"GROUP BY institution ORDER BY cnt DESC LIMIT 1", (week_ago,)) or [{}]
            )[0].get("institution", "없음"),
            "total_budget": scalar(
                f"SELECT SUM(COALESCE(budget,0)) FROM announcements "
                f"WHERE score>=5 AND reg_date>=? AND {NOT_DELETED}", (thirty_ago,)
            ) or 0,
            "new_this_week": scalar(
                f"SELECT COUNT(*) FROM announcements WHERE reg_date>=? AND {NOT_DELETED}", (week_ago,)
            ),
            "db_total": scalar(f"SELECT COUNT(*) FROM announcements WHERE {NOT_DELETED}"),
        },

        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": f"{thirty_ago} ~ {today}",
    }

    conn.close()
    return data


def git_push():
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "add", "data/announcements.json"], cwd=repo_dir, check=True)
        msg = f"데이터 업데이트 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "commit", "-m", msg], cwd=repo_dir, check=True)
        subprocess.run(["git", "push"], cwd=repo_dir, check=True)
        print("✅  GitHub에 push 완료")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  git 오류: {e}")
        print("    수동으로 push 해주세요: cd g2b-dashboard && git push")


def _score_to_priority(score, end_date=None):
    if end_date and end_date != "-":
        try:
            days_left = (datetime.datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.date.today()).days
            if days_left <= 3 and score >= 7:
                return 5
            if days_left <= 7 and score >= 7:
                return 4
        except Exception:
            pass
    if score >= 9: return 3
    if score >= 7: return 2
    return 1


def _build_description(row):
    return (
        "## 입찰 정보\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 발주기관 | {row['institution']} |\n"
        f"| 공고일   | {row['reg_date']} |\n"
        f"| 마감일   | {row['end_date']} |\n"
        f"| AI 점수  | {row['score']}점 |\n"
        f"| 카테고리 | {row['category_tag']} |\n"
        f"| 출처     | {row.get('source','G2B')} |\n\n"
        "## AI 분석\n"
        f"{row['ai_reason']}\n\n"
        "## 링크\n"
        f"{row['link']}\n"
    )


def push_to_redmine():
    """Top 후보(5점+) 중 미등록 항목을 Redmine 새기능 트래커에 누적 등록"""
    if not os.path.exists(DB_PATH):
        return

    thirty_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, institution, title, reg_date, end_date, link, score, category_tag, ai_reason, "
        "COALESCE(source,'G2B') AS source "
        "FROM announcements "
        "WHERE score >= 5 AND reg_date >= ? "
        "AND (is_deleted IS NULL OR is_deleted=0) "
        "AND (redmine_id IS NULL OR redmine_id = 0) "
        "ORDER BY score DESC",
        (thirty_ago,)
    ).fetchall()

    if not rows:
        print("   Redmine: 신규 등록 대상 없음 (모두 기등록)")
        conn.close()
        return

    print(f"   Redmine: {len(rows)}건 새기능 등록 시작...")
    ok = 0
    for row in rows:
        row = dict(row)
        body = {
            "issue": {
                "project_id":  REDMINE_PROJECT,
                "tracker_id":  TRACKER_ID,
                "subject":     f"[{row['score']}점][G2B] {row['title']}",
                "description": _build_description(row),
                "priority_id": _score_to_priority(row["score"], row.get("end_date", "-")),
            }
        }
        try:
            r = requests.post(f"{REDMINE_URL}/issues.json", json=body,
                              auth=REDMINE_AUTH, timeout=10)
            if r.status_code in (200, 201):
                issue_id = r.json()["issue"]["id"]
                cur.execute("UPDATE announcements SET redmine_id=? WHERE id=?",
                            (issue_id, row["id"]))
                conn.commit()
                ok += 1
                print(f"   ✅ #{issue_id} [{row['score']}점] {row['title'][:50]}")
            else:
                print(f"   ⚠️  HTTP {r.status_code} — {row['title'][:40]}")
        except Exception as e:
            print(f"   ❌ 오류: {e}")

    conn.close()
    print(f"   Redmine 등록 완료: {ok}/{len(rows)}건")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    _migrate_db()

    print("📦  DB 읽는 중...")
    data = build_json()

    print(f"💾  JSON 저장: {OUT_FILE}")
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    kpi = data["kpi"]
    print(f"\n📊  내보내기 완료")
    print(f"   기간      : {data['period']}")
    print(f"   전체 누적 : {kpi['total']}건")
    print(f"   최근 30일 : {kpi['new_week']}건")
    print(f"   Top 후보  : {kpi['top7']}건 (5점+)")
    print(f"   마감임박  : {kpi['deadline']}건 (14일 내)")

    ci_mode = "--ci" in sys.argv          # GitHub Actions: git·Redmine 모두 건너뜀 (workflow가 처리)
    push    = "-y" in sys.argv or "--yes" in sys.argv

    if ci_mode:
        print("\nℹ️  CI 모드: git push·Redmine 건너뜀 (workflow에서 일괄 처리)")
    elif push:
        git_push()
        print("\n🔗  Redmine 새기능 등록 중...")
        push_to_redmine()
    else:
        ans = input("\n⬆️  GitHub push + Redmine 등록 할까요? (y/N): ").strip().lower()
        if ans == "y":
            git_push()
            print("\n🔗  Redmine 새기능 등록 중...")
            push_to_redmine()
        else:
            print("\n수동 실행:  python g2b_export_json.py -y")


if __name__ == "__main__":
    main()
