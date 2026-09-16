"""검색 품질 지표 — 정답 라벨셋 기반 precision@k / contradiction_coverage.

ADR-019/029에서 `retrieval_metrics`가 `None + TODO`로 남아 있던 자리를 채운다.
"이 쿼리엔 이 문서가 적합"이라는 정답이 있어야 계산할 수 있어 미뤄져 있었다.

설계 포인트:
- **에이전트와 같은 검색 경로를 쓴다.** `retrieval.tools.retrieve()`를 그대로 호출하므로
  하이브리드 검색 + rerank까지 포함한 실제 품질을 잰다. 별도 검색 코드를 두면
  "하네스에서만 좋은 숫자"가 나오므로 의도적으로 재사용한다.
- **LLM을 안 쓴다.** 로컬 임베딩 + DB 검색만 하므로 Bedrock 비용이 들지 않는다.
  하네스의 다른 지표(비용·판정)와 달리 반복 실행 부담이 없다.
- **미라벨 문서를 숨기지 않는다.** top-k에 라벨이 없는 문서가 섞이면 precision이
  과소평가된다. 그 개수를 함께 반환해 수치를 해석할 수 있게 한다.

라벨셋 포맷은 `eval/labels/README.md` 참조. 후보 생성은 `eval/build_labelset.py`.
"""
from __future__ import annotations

import json
import math
import pathlib
from typing import Any

from retrieval.tools import retrieve

# 라벨셋 기본 위치. 팀/개인마다 다른 셋을 쓸 수 있어 경로는 인자로도 받는다.
DEFAULT_LABELSET = pathlib.Path(__file__).parent / "labels" / "retrieval_labels.json"

# precision@k의 기본 k. 에이전트가 실제로 프롬프트에 넣는 근거 수(retrieve의 k=5)와 맞춘다.
DEFAULT_K = 5


def load_labelset(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """라벨셋 JSON을 읽고 최소 스키마를 검증한다.

    기본 경로를 인자 기본값으로 묶지 않는다 — 그러면 정의 시점에 고정돼
    테스트에서 DEFAULT_LABELSET을 바꿔치기해도 반영되지 않는다.
    """
    path = pathlib.Path(path if path is not None else DEFAULT_LABELSET)
    if not path.exists():
        raise FileNotFoundError(
            f"라벨셋이 없다: {path}\n"
            "eval/build_labelset.py로 후보를 생성한 뒤 relevant/stance를 채워라."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    queries = data.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError(f"{path}: 'queries'가 비어 있다.")
    for query in queries:
        for field in ("query_id", "query", "labels"):
            if field not in query:
                raise ValueError(f"{path}: 쿼리에 '{field}'가 없다 — {query.get('query_id')}")
    return data


def _labeled_relevant(query: dict[str, Any]) -> set[str]:
    return {
        doc_id
        for doc_id, label in query["labels"].items()
        if label.get("relevant") is True
    }


def _labeled_stance(query: dict[str, Any], stance: str) -> set[str]:
    return {
        doc_id
        for doc_id, label in query["labels"].items()
        if label.get("stance") == stance
    }


def _retrieve_ids(query: dict[str, Any], k: int) -> list[str]:
    """에이전트와 동일한 경로로 검색해 document_id를 순위대로 돌려준다."""
    items = retrieve(
        query.get("axis", "H0"),
        query["query"],
        k=k,
        source_types=query.get("source_types"),
    )
    return [item.document_id for item in items]


def _dcg(gains: list[float]) -> float:
    """할인 누적 이득. 상위에 있을수록 가중치가 크다(log2(rank+1)로 나눈다)."""
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def _rank_metrics(retrieved: list[str], relevant: set[str]) -> dict[str, Any]:
    """순위를 고려한 지표 묶음. 이진 관련도(relevant 여부)를 가정한다.

    - precision@k : top-k 중 맞은 비율. "보여준 것이 쓸모 있었나"
    - recall@k    : 정답 중 top-k에 들어온 비율. "놓친 게 없나"
      ⚠️ **풀(pool) 기준이다.** 라벨은 검색 상위 10건에만 달려 있으므로
      "코퍼스 전체의 정답"을 알 수 없다. 절대 recall이 아니라 풀 안에서의
      recall이며, 실제 값보다 높게 나온다. 해석할 때 반드시 감안할 것.
    - MRR         : 첫 정답이 몇 등에 있었나(1/rank). 상위 노출 품질.
    - NDCG@k      : 정답이 상위에 몰려 있을수록 1에 가깝다.
    """
    hits = [1.0 if doc_id in relevant else 0.0 for doc_id in retrieved]
    n_hit = int(sum(hits))

    first = next((i for i, h in enumerate(hits) if h), None)
    ideal = sorted(hits, reverse=True)
    idcg = _dcg(ideal)

    return {
        "precision_at_k": round(n_hit / len(retrieved), 3) if retrieved else 0.0,
        # 정답이 하나도 라벨링되지 않았으면 0.0이 아니라 None — 0.0은 "다 놓쳤다"로 읽힌다
        "recall_at_k_pooled": round(n_hit / len(relevant), 3) if relevant else None,
        "reciprocal_rank": round(1.0 / (first + 1), 3) if first is not None else 0.0,
        "ndcg_at_k": round(_dcg(hits) / idcg, 3) if idcg else 0.0,
    }


def _mean(values: list[float]) -> float | None:
    """None을 뺀 평균. 전부 None이면 None(0.0으로 뭉개지 않는다)."""
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def evaluate_retrieval(
    path: str | pathlib.Path | None = None,
    k: int = DEFAULT_K,
) -> dict[str, Any]:
    """라벨셋 전체에 대해 precision@k와 contradiction_coverage를 계산한다.

    - precision@k          : top-k 중 relevant 비율. 쿼리별로 구해 macro 평균.
    - contradiction_coverage: 라벨에 contradicts로 표시된 문서 중 top-k에 들어온 비율.
      Evidence Board가 "상충 근거를 드러낸다"고 주장하려면 이 값이 받쳐줘야 한다.
    - unlabeled_in_topk    : top-k에 들어왔지만 라벨이 없는 문서 수. 크면 precision이
      과소평가된 것이니 라벨을 더 채워야 한다.
    """
    data = load_labelset(path)
    per_query: list[dict[str, Any]] = []

    for query in data["queries"]:
        relevant = _labeled_relevant(query)
        contradicting = _labeled_stance(query, "contradicts")
        retrieved = _retrieve_ids(query, k)
        labeled_ids = set(query["labels"])

        unlabeled = [doc_id for doc_id in retrieved if doc_id not in labeled_ids]

        per_query.append({
            "query_id": query["query_id"],
            "axis": query.get("axis"),
            # 분모는 k가 아니라 min(k, 검색된 수) — DB에 문서가 k개보다 적을 수 있다.
            **_rank_metrics(retrieved, relevant),
            "retrieved": len(retrieved),
            "relevant_labeled": len(relevant),
            "contradiction_hits": len(contradicting & set(retrieved)),
            "contradiction_labeled": len(contradicting),
            "unlabeled_in_topk": len(unlabeled),
        })

    contra_labeled = sum(q["contradiction_labeled"] for q in per_query)
    contra_hits = sum(q["contradiction_hits"] for q in per_query)

    return {
        "k": k,
        "queries": len(per_query),
        "precision_at_k": _mean([q["precision_at_k"] for q in per_query]),
        "recall_at_k_pooled": _mean([q["recall_at_k_pooled"] for q in per_query]),
        "mrr": _mean([q["reciprocal_rank"] for q in per_query]),
        "ndcg_at_k": _mean([q["ndcg_at_k"] for q in per_query]),
        # 반박 근거를 라벨링하지 않았으면 None — 0.0으로 쓰면 "못 찾았다"로 오해된다.
        "contradiction_coverage": (
            round(contra_hits / contra_labeled, 3) if contra_labeled else None
        ),
        "unlabeled_in_topk": sum(q["unlabeled_in_topk"] for q in per_query),
        "per_query": per_query,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_retrieval(), ensure_ascii=False, indent=2))
