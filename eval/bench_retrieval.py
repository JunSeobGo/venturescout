"""검색 지연 실측 — p50 / p95 / p99.

체크리스트의 "운영 성능"에서 **검색 구간만** 떼어내 잰다. 에이전트 전체 지연은
LLM이 지배하지만, 검색은 LLM 없이 측정할 수 있어 비용 0으로 바로 낼 수 있다.

측정 대상을 둘로 나눈다 — 합쳐놓으면 어디가 느린지 모른다:
  - embed : 쿼리 문장을 768d 벡터로 만드는 시간(로컬 PatentSBERTa, CPU)
  - total : retrieve() 전체(embed + 하이브리드 SQL + rerank). 에이전트가 겪는 값
  - sql   : total - embed 로 추정. 정확한 분리가 아니라 근사다

    python -m eval.bench_retrieval --runs 30

주의: 첫 회는 모델 로드·커넥션 수립이 섞이므로 워밍업으로 빼고 잰다.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time

from eval.build_labelset import QUERIES
from retrieval.tools import _get_engines, retrieve


def _pct(values: list[float], p: float) -> float:
    """오름차순 정렬 후 p분위. numpy 없이 가장 가까운 순위값을 쓴다."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(round(p / 100 * (len(ordered) - 1))), len(ordered) - 1)
    return round(ordered[idx] * 1000, 1)      # ms


def _summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": _pct(values, 50),
        "p95_ms": _pct(values, 95),
        "p99_ms": _pct(values, 99),
        "mean_ms": round(statistics.fmean(values) * 1000, 1) if values else 0.0,
        "min_ms": round(min(values) * 1000, 1) if values else 0.0,
        "max_ms": round(max(values) * 1000, 1) if values else 0.0,
    }


def bench(runs: int, k: int, warmup: int) -> dict:
    searcher, _ = _get_engines()

    print(f"워밍업 {warmup}회 (모델 로드·커넥션 수립 제외)...")
    for spec in QUERIES[:warmup] or QUERIES[:1]:
        retrieve(spec["axis"], spec["query"], k=k, source_types=spec.get("source_types"))

    total_all: list[float] = []
    embed_all: list[float] = []
    per_query = []

    for spec in QUERIES:
        totals, embeds = [], []
        for _ in range(runs):
            t0 = time.perf_counter()
            searcher.embedder.embed(spec["query"])
            embeds.append(time.perf_counter() - t0)

            t1 = time.perf_counter()
            retrieve(spec["axis"], spec["query"], k=k, source_types=spec.get("source_types"))
            totals.append(time.perf_counter() - t1)

        total_all += totals
        embed_all += embeds
        s_total, s_embed = _summary(totals), _summary(embeds)
        per_query.append({
            "query_id": spec["query_id"],
            "scope": spec.get("source_types"),
            "total_p50_ms": s_total["p50_ms"],
            "total_p95_ms": s_total["p95_ms"],
            "embed_p50_ms": s_embed["p50_ms"],
            "sql_p50_ms": round(s_total["p50_ms"] - s_embed["p50_ms"], 1),
        })
        print(f"  {spec['query_id']:<12} total p50 {s_total['p50_ms']:>7}ms  "
              f"p95 {s_total['p95_ms']:>7}ms  (embed {s_embed['p50_ms']}ms)")

    embed_p50 = _summary(embed_all)["p50_ms"]
    total_p50 = _summary(total_all)["p50_ms"]
    return {
        "k": k,
        "runs_per_query": runs,
        "queries": len(QUERIES),
        "total": _summary(total_all),
        "embed": _summary(embed_all),
        # 근사치 — embed와 total을 따로 잰 값의 차이라 정확한 구간 분리가 아니다
        "sql_p50_ms_approx": round(total_p50 - embed_p50, 1),
        "per_query": per_query,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="검색 지연 실측 (LLM 미사용)")
    p.add_argument("--runs", type=int, default=20, help="쿼리당 반복 횟수")
    p.add_argument("--k", type=int, default=5, help="검색 top-k")
    p.add_argument("--warmup", type=int, default=2, help="워밍업 횟수")
    args = p.parse_args()

    result = bench(args.runs, args.k, args.warmup)
    t, e = result["total"], result["embed"]
    print(f"\n=== 전체 (쿼리 {result['queries']}개 × {args.runs}회 = {t['n']}샘플) ===")
    print(f"  total  p50 {t['p50_ms']}ms / p95 {t['p95_ms']}ms / p99 {t['p99_ms']}ms")
    print(f"  embed  p50 {e['p50_ms']}ms / p95 {e['p95_ms']}ms")
    print(f"  sql≈   p50 {result['sql_p50_ms_approx']}ms")
    print()
    print(json.dumps({k: v for k, v in result.items() if k != "per_query"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
