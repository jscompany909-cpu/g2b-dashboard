"""
g2b_harness.py — 이노티움 G2B 수집 파이프라인 평가·검증 하네스

실행 방법:
  python g2b_harness.py               # 수집 포함 전체 실행 (최대 3라운드)
  python g2b_harness.py --no-collect  # 수집 건너뛰고 검증만
  python g2b_harness.py 5             # 최대 5라운드

라운드 구조 (목표 신뢰도 도달 시 조기 종료):
  Phase 1: 평가(Eval)    — 현재 점수 분포·불확실 항목 비율 측정
  Phase 2: 검증(Verify)  — 불확실 항목(4~7점)을 3회 재채점해 일관성 확인
  Phase 3: 재채점(Rescore) — 중앙값으로 DB 갱신
  반복 후 최종 보고: 라운드별 신뢰도 변화 + 최근 1개월 요약
"""

import io
import os
import sqlite3
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from dotenv import load_dotenv

# Windows cp949 터미널에서 유니코드 출력 안전 처리
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "정부과제_트렌드_창고.db")

MAX_ROUNDS        = 3      # 기본 최대 라운드
CONFIDENCE_TARGET = 0.80   # 80% 달성 시 조기 종료
MULTI_PASS        = 3      # 불확실 항목 재채점 횟수
UNCERTAIN_LO      = 4      # 불확실 구간 하한 (이상)
UNCERTAIN_HI      = 7      # 불확실 구간 상한 (이하)
MAX_RESCORE_PER_ROUND = 50 # 라운드당 최대 재채점 건수 (속도 조절)
THIRTY_AGO        = (date.today() - timedelta(days=30)).isoformat()


# ─────────────────────────────────────────────────────────
# 공통
# ─────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _print_section(title: str):
    print(f"\n{'─' * 62}")
    print(f"  {title}")
    print(f"{'─' * 62}")


# ─────────────────────────────────────────────────────────
# Phase 0: 수집
# ─────────────────────────────────────────────────────────

def phase_collect():
    _print_section("Phase 0 ▶ 나라장터 + KISA/NIPA/IITP 수집")
    from g2b_bid_collector import run_innotium_reinforced_pipeline
    run_innotium_reinforced_pipeline()


# ─────────────────────────────────────────────────────────
# Phase 1: 평가
# ─────────────────────────────────────────────────────────

def phase_eval(round_num: int) -> dict:
    _print_section(f"Round {round_num} — Phase 1 ▶ 평가 (Evaluation)")

    if not os.path.exists(DB_PATH):
        print("  ⚠️  DB 없음 — 수집을 먼저 실행하세요.")
        return {"total": 0, "confidence": 0.0, "uncertain": [], "dist": {}}

    conn = _get_db()
    rows = conn.execute(
        "SELECT id, title, score FROM announcements "
        "WHERE reg_date >= ? AND score IS NOT NULL AND (is_deleted IS NULL OR is_deleted=0) "
        "ORDER BY score DESC",
        (THIRTY_AGO,)
    ).fetchall()
    conn.close()

    if not rows:
        print("  ⚠️  최근 1개월 데이터 없음")
        return {"total": 0, "confidence": 0.0, "uncertain": [], "dist": {}}

    total    = len(rows)
    scores   = [r["score"] for r in rows]
    uncertain = [(r["id"], r["title"], r["score"])
                 for r in rows if UNCERTAIN_LO <= r["score"] <= UNCERTAIN_HI]
    certain_n = total - len(uncertain)
    confidence = certain_n / total

    # 점수 분포 (2점 단위 버킷)
    dist: dict[str, int] = {}
    for s in scores:
        lo = (s // 2) * 2
        key = f"{lo}-{lo+1}" if lo < 10 else "10"
        dist[key] = dist.get(key, 0) + 1

    print(f"  대상 기간    : {THIRTY_AGO} ~ {date.today().isoformat()} (최근 30일)")
    print(f"  전체 항목    : {total}건")
    print(f"  점수 분포    : {dict(sorted(dist.items()))}")
    print(f"  확실 (0-3, 8-10) : {certain_n}건")
    print(f"  불확실 (4-7)     : {len(uncertain)}건")
    print(f"  → 현재 신뢰도    : {confidence:.1%}")

    return {
        "total": total,
        "confidence": confidence,
        "uncertain": uncertain,
        "dist": dist,
    }


# ─────────────────────────────────────────────────────────
# Phase 2: 검증
# ─────────────────────────────────────────────────────────

def phase_verify(uncertain: list, round_num: int) -> dict:
    _print_section(f"Round {round_num} — Phase 2 ▶ 검증 (Verify)")

    if not uncertain:
        print("  ✅  불확실 항목 없음 — 검증 불필요")
        return {}

    targets = uncertain[:MAX_RESCORE_PER_ROUND]
    skipped = len(uncertain) - len(targets)
    print(f"  불확실 {len(uncertain)}건 중 {len(targets)}건 재채점 ({MULTI_PASS}회/건)"
          + (f" ← 나머지 {skipped}건은 다음 라운드" if skipped else ""))

    from g2b_bid_collector import ask_local_qwen_scoring

    results: dict[int, dict] = {}

    def multi_score(item_id, title, orig_score):
        passes = []
        for _ in range(MULTI_PASS):
            s, tag, reason = ask_local_qwen_scoring(title)
            passes.append(s)
        passes.sort()
        median_s = passes[len(passes) // 2]
        std = statistics.stdev(passes) if len(passes) > 1 else 0.0
        return item_id, title, orig_score, passes, median_s, std

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(multi_score, iid, title, orig): iid
            for iid, title, orig in targets
        }
        for future in as_completed(futures):
            iid, title, orig, passes, median_s, std = future.result()
            flag = "✅" if std <= 1.5 else ("⚠️ " if std <= 3 else "❌")
            moved = f"→ {orig}→{median_s}점" if orig != median_s else f"유지 {median_s}점"
            print(f"  {flag} {passes}  std={std:.1f}  {moved}  {title[:40]}")
            results[iid] = {
                "title": title,
                "orig_score": orig,
                "passes": passes,
                "median": median_s,
                "std": std,
            }

    high_std = [v for v in results.values() if v["std"] > 2]
    moved    = [v for v in results.values() if v["median"] != v["orig_score"]]
    print(f"\n  결과 요약: 총 {len(results)}건  |  점수변경 {len(moved)}건  |  고분산(std>2) {len(high_std)}건")
    return results


# ─────────────────────────────────────────────────────────
# Phase 3: 재채점 반영
# ─────────────────────────────────────────────────────────

def phase_rescore(verify_result: dict, round_num: int) -> int:
    _print_section(f"Round {round_num} — Phase 3 ▶ DB 반영 (Rescore)")

    if not verify_result:
        print("  ⏩  반영할 항목 없음")
        return 0

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    updated = 0
    for iid, data in verify_result.items():
        if data["median"] != data["orig_score"]:
            cur.execute(
                "UPDATE announcements SET score=?, ai_reason=? WHERE id=?",
                (
                    data["median"],
                    f"[하네스 R{round_num}] passes={data['passes']} std={data['std']:.1f} → 중앙값 {data['median']}점",
                    iid,
                ),
            )
            updated += 1
    conn.commit()
    conn.close()
    print(f"  DB 업데이트: {updated}건 점수 변경")
    return updated


# ─────────────────────────────────────────────────────────
# 최종 보고
# ─────────────────────────────────────────────────────────

def final_report(round_metrics: list):
    print("\n" + "=" * 62)
    print("  📋 하네스 최종 보고")
    print("=" * 62)

    print("\n[라운드별 신뢰도 변화]")
    for m in round_metrics:
        bar = "█" * int(m["confidence"] * 30)
        print(f"  Round {m['round']}: {bar:<30} {m['confidence']:>6.1%}  ({m['total']}건)")

    if not os.path.exists(DB_PATH):
        print("\n  ⚠️  DB 없음")
        return

    conn = _get_db()

    total_month = conn.execute(
        "SELECT COUNT(*) FROM announcements WHERE reg_date >= ? AND (is_deleted IS NULL OR is_deleted=0)",
        (THIRTY_AGO,)
    ).fetchone()[0]

    dist_rows = conn.execute(
        "SELECT score, COUNT(*) AS cnt FROM announcements "
        "WHERE reg_date >= ? AND (is_deleted IS NULL OR is_deleted=0) "
        "GROUP BY score ORDER BY score DESC",
        (THIRTY_AGO,)
    ).fetchall()

    top_rows = conn.execute(
        "SELECT institution, title, score, end_date, category_tag, source "
        "FROM announcements "
        "WHERE reg_date >= ? AND score >= 7 AND (is_deleted IS NULL OR is_deleted=0) "
        "ORDER BY score DESC LIMIT 20",
        (THIRTY_AGO,)
    ).fetchall()

    conn.close()

    print(f"\n[최근 1개월 데이터 — {THIRTY_AGO} ~ {date.today().isoformat()}]")
    print(f"  총 수집 건수: {total_month}건")

    print("\n  점수 분포:")
    for r in dist_rows:
        bar = "■" * min(r["cnt"], 40)
        print(f"    {r['score']:>2}점 | {bar:<40} {r['cnt']}건")

    if top_rows:
        print(f"\n  🎯 Top 후보 (7점 이상) — {len(top_rows)}건:")
        print(f"  {'점수':>4}  {'마감일':<12}  {'출처':<5}  {'기관':<18}  제목")
        print(f"  {'─'*4}  {'─'*12}  {'─'*5}  {'─'*18}  {'─'*35}")
        for r in top_rows:
            inst = (r["institution"] or "")[:16]
            src  = (r["source"] or "G2B")[:5]
            print(f"  {r['score']:>4}점  {r['end_date']:<12}  {src:<5}  {inst:<18}  {r['title'][:45]}")
    else:
        print("  ⚠️  7점 이상 항목 없음 (수집 후 재시도)")

    print("\n" + "=" * 62)
    print("  ✅ 하네스 완료")
    print("=" * 62)


# ─────────────────────────────────────────────────────────
# 메인 하네스 실행
# ─────────────────────────────────────────────────────────

def run_harness(collect: bool = True, max_rounds: int = MAX_ROUNDS):
    print("=" * 62)
    print("  🚀 이노티움 G2B 하네스 시작")
    print(f"     최대 라운드: {max_rounds}  |  목표 신뢰도: {CONFIDENCE_TARGET:.0%}")
    print(f"     대상 기간  : 최근 1개월 ({THIRTY_AGO} ~)")
    print("=" * 62)

    if collect:
        phase_collect()

    round_metrics = []

    for rnd in range(1, max_rounds + 1):
        eval_result = phase_eval(rnd)
        round_metrics.append({
            "round": rnd,
            "total": eval_result["total"],
            "confidence": eval_result["confidence"],
        })

        if eval_result["total"] == 0:
            print("  ❌  데이터 없음 — 하네스 종료")
            break

        if eval_result["confidence"] >= CONFIDENCE_TARGET:
            print(f"\n  ✅  목표 신뢰도 {CONFIDENCE_TARGET:.0%} 달성 → Round {rnd} 완료 후 조기 종료")
            break

        verify_result = phase_verify(eval_result["uncertain"], rnd)
        updated       = phase_rescore(verify_result, rnd)

        if updated == 0 and rnd > 1:
            print("\n  ✅  더 이상 변경 항목 없음 — 수렴")
            break

    final_report(round_metrics)


# ─────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    collect    = "--no-collect" not in sys.argv
    rounds_arg = next((a for a in sys.argv[1:] if a.isdigit()), None)
    max_rounds = int(rounds_arg) if rounds_arg else MAX_ROUNDS
    run_harness(collect=collect, max_rounds=max_rounds)
