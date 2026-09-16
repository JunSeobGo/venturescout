"""rerank 가중치 스윕 — "왜 0.4/0.3/0.1/0.2인가"에 측정 근거를 만든다.

현재 가중치는 측정 없이 정해졌다(ADR §5 open). 라벨셋이 있으면 조합을 바꿔가며
precision@k·NDCG를 재서 근거 있는 값으로 바꿀 수 있다.

    python -m eval.sweep_rerank                 # 기본 후보 조합
    python -m eval.sweep_rerank --k 5 --top 5   # 상위 5개만 출력

주의:
- **LLM을 쓰지 않는다.** 검색 + rerank만 돌리므로 비용 0.
- `hybrid_score`의 0.6/0.4(vector/keyword)는 SQL 안에 있어 여기서 못 바꾼다.
  이 스윕이 다루는 건 rerank 4축(relevance/reliability/freshness/contradiction)뿐이다.
- 쿼리당 검색을 1회만 돌리고 그 후보를 조합별로 재정렬한다. DB를 조합 수만큼
  때리면 느리고, 같은 후보에 대한 정렬 차이만 보면 되기 때문이다.
"""
from __future__ import annotations

import argparse
import itertools
import json

from eval.labelset import _labeled_relevant, _mean, _rank_metrics, load_labelset
from retrieval.tools import _get_engines
from search.reranker import ReRanker

# 스윕할 가중치 후보. 합이 1이 되도록 조합한다.
# 현재 운영값(0.4/0.3/0.1/0.2)이 후보에 포함되도록 눈금을 잡았다.
GRID = {
    "relevance": [0.3, 0.4, 0.5, 0.6],
    "reliability": [0.1, 0.2, 0.3],
    "freshness": [0.0, 0.1],
    "contradiction": [0.1, 0.2, 0.3],
}
CURRENT = {"relevance": 0.4, "reliability": 0.3, "freshness": 0.1, "contradiction": 0.2}


def _combos() -> list[dict[str, float]]:
    """합이 1.0인 조합만 남긴다(가중치 스케일이 제각각이면 비교가 안 된다)."""
    keys = list(GRID)
    out = []
    for values in itertools.product(*(GRID[k] for k in keys)):
        combo = dict(zip(keys, values))
        if abs(sum(values) - 1.0) < 1e-9:
            out.append(combo)
    return out


def _fetch_pools(data: dict, pool_k: int) -> list[tuple[dict, list[dict]]]:
    """쿼리마다 검색 후보를 한 번만 가져온다(조합마다 DB를 때리지 않으려고)."""
    searcher, _ = _get_engines()
    pools = []
    for query in data["queries"]:
        raw = searcher.search_documents(
            query=query["query"],
            top_k=pool_k,
            source_types=query.get("source_types"),
        )
        pools.append((query, raw))
    return pools


def sweep(k: int = 5, pool_k: int = 20) -> list[dict]:
    data = load_labelset()
    pools = _fetch_pools(data, pool_k)

    results = []
    for combo in _combos():
        reranker = ReRanker(**{f"{name}_w": w for name, w in combo.items()})
        per_query = []
        for query, raw in pools:
            ranked = reranker.rerank(raw, prefer_contradicting=True, top_k=k)
            retrieved = [str(item["document_id"]) for item in ranked]
            per_query.append(_rank_metrics(retrieved, _labeled_relevant(query)))

        results.append({
            **combo,
            "is_current": combo == CURRENT,
            "precision_at_k": _mean([q["precision_at_k"] for q in per_query]),
            "ndcg_at_k": _mean([q["ndcg_at_k"] for q in per_query]),
            "mrr": _mean([q["reciprocal_rank"] for q in per_query]),
        })

    # NDCG를 1순위로 정렬 — 개수(precision)보다 "상위에 몰았는가"가 검색 품질에 가깝다
    results.sort(key=lambda r: (r["ndcg_at_k"] or 0, r["precision_at_k"] or 0), reverse=True)
    return results


def main() -> None:
    p = argparse.ArgumentParser(description="rerank 가중치 스윕 (LLM 미사용)")
    p.add_argument("--k", type=int, default=5, help="평가 기준 top-k")
    p.add_argument("--pool", type=int, default=20, help="재정렬 대상 후보 수")
    p.add_argument("--top", type=int, default=10, help="출력할 상위 조합 수")
    args = p.parse_args()

    rows = sweep(k=args.k, pool_k=args.pool)
    if not rows:
        raise SystemExit("합이 1.0인 조합이 없다. GRID를 확인해라.")

    print(f"조합 {len(rows)}개 / k={args.k}\n")
    header = f"{'rel':>5}{'rel(도)':>8}{'fresh':>7}{'contra':>8}  {'NDCG':>6}{'P@k':>7}{'MRR':>7}"
    print(header.replace("rel(도)", "relia"))
    print("-" * len(header))
    for r in rows[:args.top]:
        mark = " ←현재" if r["is_current"] else ""
        print(f"{r['relevance']:>5}{r['reliability']:>8}{r['freshness']:>7}"
              f"{r['contradiction']:>8}  {r['ndcg_at_k']:>6}{r['precision_at_k']:>7}"
              f"{r['mrr']:>7}{mark}")

    current = next((r for r in rows if r["is_current"]), None)
    if current:
        rank = rows.index(current) + 1
        print(f"\n현재 운영값은 {len(rows)}개 중 {rank}위 "
              f"(NDCG {current['ndcg_at_k']}, P@{args.k} {current['precision_at_k']})")
    print("\n" + json.dumps(rows[0], ensure_ascii=False))


if __name__ == "__main__":
    main()
