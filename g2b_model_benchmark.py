"""
g2b_model_benchmark.py — GPT-4o vs o4-mini 채점 정합성 벤치마크

같은 공고를 3회 반복 채점해 일관성(표준편차)과 Qwen 기존 점수와의 차이를 비교
"""

import re
import sqlite3
import statistics
import time
import os

from openai import AzureOpenAI

from dotenv import load_dotenv
load_dotenv()

DB_PATH        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "정부과제_트렌드_창고.db")
AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_KEY      = os.environ.get("AZURE_OPENAI_KEY", "")
RUNS           = 3   # 항목당 반복 채점 횟수
N_PER_BAND     = 5   # 점수 대역별 샘플 수

MODELS = {
    "gpt-4o": {
        "api_version":  "2025-01-01-preview",
        "is_reasoning": False,
    },
    "o4-mini": {
        "api_version":  "2025-04-01-preview",
        "is_reasoning": True,
    },
}

PROMPT = """당신은 이노티움(Innotium) 사업개발팀 전문가입니다.

[이노티움 핵심 사업]
보안 소프트웨어/솔루션을 납품·구축하는 기업입니다.
컨설팅·감사·관제·진단·교육은 우리 사업이 아닙니다.

[납품 가능 제품군]
- 문서중앙화(ECM), DLP/정보유출방지, 화면보안, 워터마크, 캡처방지
- 랜섬웨어 탐지·차단, 보안백업, 제로트러스트/N2SF, SBOM, 개인정보보호 솔루션

[채점 기준]
9~10점: 이노티움 제품 직접 납품 가능
7~8점 : 보안 솔루션 구축·도입 관련
5~6점 : 일부 포함 가능성
2~4점 : 낮은 관련성
0~1점 : 무관 또는 컨설팅/감사/교육/관제 전용

[반드시 0~1점]
보안 컨설팅, 취약점 진단, 모의해킹, 관제 서비스, 인증 지원, 교육·훈련

[평가 대상 공고]
{title}

아래 형식으로만 출력:
SCORE: 숫자
TAG: 키워드
REASON: 한줄이유"""


def get_client(api_version: str) -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
        api_version=api_version,
    )


def score_once(title: str, model_name: str, is_reasoning: bool, api_version: str) -> int:
    client = get_client(api_version)
    prompt = PROMPT.format(title=title)
    try:
        if is_reasoning:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=300,
            )
        else:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100,
            )
        text = resp.choices[0].message.content.strip()
        for line in text.split("\n"):
            if line.strip().startswith("SCORE:"):
                m = re.search(r"\b(\d+)\b", line)
                if m:
                    return min(10, max(0, int(m.group(1))))
    except Exception as e:
        print(f"    오류: {e}")
    return -1


def sample_items():
    """점수 대역별 샘플 추출 (균형 있는 테스트 셋)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    items = []
    for lo, hi in [(0, 1), (2, 3), (4, 7), (8, 10)]:
        rows = conn.execute(
            "SELECT id, title, score FROM announcements "
            "WHERE score BETWEEN ? AND ? AND (is_deleted IS NULL OR is_deleted=0) "
            "ORDER BY RANDOM() LIMIT ?",
            (lo, hi, N_PER_BAND)
        ).fetchall()
        items.extend([dict(r) for r in rows])
    conn.close()
    return items


def run_benchmark():
    print("=" * 68)
    print("  GPT-4o  vs  o4-mini  채점 정합성 벤치마크")
    print("=" * 68)

    items = sample_items()
    total = len(items)
    print(f"\n테스트 셋: {total}건 (점수 대역 0~1 / 2~3 / 4~7 / 8~10, 각 {N_PER_BAND}건)")
    print(f"반복 횟수: {RUNS}회/건  |  총 API 호출: {total * RUNS * 2}회\n")

    all_results = {}

    for model_name, cfg in MODELS.items():
        print(f"\n{'─'*68}")
        print(f"  [{model_name}] 테스트 중...")
        print(f"{'─'*68}")
        model_results = []

        for item in items:
            title    = item["title"]
            orig     = item["score"]
            scores   = []
            t0       = time.time()

            for _ in range(RUNS):
                s = score_once(title, model_name, cfg["is_reasoning"], cfg["api_version"])
                if s >= 0:
                    scores.append(s)

            elapsed = time.time() - t0

            if len(scores) >= 2:
                std  = statistics.stdev(scores)
                mean = round(statistics.mean(scores), 1)
                diff = abs(mean - orig)
                model_results.append({
                    "title":   title,
                    "orig":    orig,
                    "scores":  scores,
                    "mean":    mean,
                    "std":     std,
                    "diff":    diff,
                    "elapsed": elapsed,
                })
                flag = "✅" if std <= 1.0 else ("⚠️ " if std <= 2.0 else "❌")
                print(f"  {flag} 원래:{orig:>2}  결과:{scores}  std={std:.1f}  차이={diff:.1f}  "
                      f"{title[:35]}")

        all_results[model_name] = model_results

    # ── 최종 분석 ──────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("  최종 비교 분석")
    print("=" * 68)

    summary = {}
    for model_name, data in all_results.items():
        if not data:
            continue
        avg_std   = statistics.mean(d["std"]     for d in data)
        avg_diff  = statistics.mean(d["diff"]    for d in data)
        avg_time  = statistics.mean(d["elapsed"] for d in data) / RUNS
        consist_n = sum(1 for d in data if d["std"] <= 1.0)
        consist_r = consist_n / len(data)
        agree_n   = sum(1 for d in data if d["diff"] <= 1.5)
        agree_r   = agree_n / len(data)

        summary[model_name] = {
            "consist_rate": consist_r,
            "avg_std":      avg_std,
            "avg_diff":     avg_diff,
            "avg_time":     avg_time,
            "agree_rate":   agree_r,
            "n":            len(data),
        }

        print(f"\n  [{model_name}]  ({len(data)}건)")
        print(f"    일관성 (std≤1.0 비율)   : {consist_r:>6.1%}  ({consist_n}/{len(data)}건)")
        print(f"    평균 표준편차            : {avg_std:>6.2f}")
        print(f"    Qwen 점수 동의율 (±1.5) : {agree_r:>6.1%}  ({agree_n}/{len(data)}건)")
        print(f"    Qwen과 평균 점수 차이   : {avg_diff:>6.2f}점")
        print(f"    평균 응답 속도           : {avg_time:>6.1f}초/건")

    # ── 추천 ───────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("  추천 결과")
    print("=" * 68)

    # 종합 점수: 일관성 50% + Qwen 동의율 30% + 속도 20%
    def score_model(s):
        return s["consist_rate"] * 0.5 + s["agree_rate"] * 0.3 + (1 / (s["avg_time"] + 1)) * 0.2

    ranked = sorted(summary.items(), key=lambda x: score_model(x[1]), reverse=True)
    winner_name, w = ranked[0]
    loser_name,  l = ranked[1]

    print(f"\n  ✅  추천 모델: {winner_name}")
    print(f"\n  비교 요약:")
    print(f"  {'항목':<22}  {winner_name:>10}  {loser_name:>10}")
    print(f"  {'─'*22}  {'─'*10}  {'─'*10}")
    print(f"  {'일관성 (std≤1 비율)':<22}  {w['consist_rate']:>9.1%}  {l['consist_rate']:>9.1%}")
    print(f"  {'평균 표준편차':<22}  {w['avg_std']:>10.2f}  {l['avg_std']:>10.2f}")
    print(f"  {'Qwen 동의율 (±1.5점)':<22}  {w['agree_rate']:>9.1%}  {l['agree_rate']:>9.1%}")
    print(f"  {'평균 응답 속도':<22}  {w['avg_time']:>9.1f}초  {l['avg_time']:>9.1f}초")
    print(f"\n  판단 근거: 같은 공고를 반복 채점했을 때 {winner_name}이")
    print(f"  더 일관된 점수를 내며 기존 Qwen 채점과도 높은 일치율을 보임")
    print("=" * 68)


if __name__ == "__main__":
    run_benchmark()
