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
import pathlib
from typing import Any

from retrieval.tools import retrieve

# 라벨셋 기본 위치. 팀/개인마다 다른 셋을 쓸 수 있어 경로는 인자로도 받는다.
DEFAULT_LABELSET = pathlib.Path(__file__).parent / "labels" / "retrieval_labels.json"

# precision@k의 기본 k. 에이전트가 실제로 프롬프트에 넣는 근거 수(retrieve의 k=5)와 맞춘다.
DEFAULT_K = 5


def load_labelset(path: str | pathlib.Path = DEFAULT_LABELSET) -> dict[str, Any]:
    """라벨셋 JSON을 읽고 최소 스키마를 검증한다."""
    path = pathlib.Path(path)
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


def evaluate_retrieval(
    path: str | pathlib.Path = DEFAULT_LABELSET,
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

        hits = [doc_id for doc_id in retrieved if doc_id in relevant]
        unlabeled = [doc_id for doc_id in retrieved if doc_id not in labeled_ids]

        per_query.append({
            "query_id": query["query_id"],
            "axis": query.get("axis"),
            # 분모는 k가 아니라 min(k, 검색된 수) — DB에 문서가 k개보다 적을 수 있다.
            "precision_at_k": round(len(hits) / len(retrieved), 3) if retrieved else 0.0,
            "retrieved": len(retrieved),
            "relevant_labeled": len(relevant),
            "contradiction_hits": len(contradicting & set(retrieved)),
            "contradiction_labeled": len(contradicting),
            "unlabeled_in_topk": len(unlabeled),
        })

    precisions = [q["precision_at_k"] for q in per_query]
    contra_labeled = sum(q["contradiction_labeled"] for q in per_query)
    contra_hits = sum(q["contradiction_hits"] for q in per_query)

    return {
        "k": k,
        "queries": len(per_query),
        "precision_at_k": round(sum(precisions) / len(precisions), 3) if precisions else 0.0,
        # 반박 근거를 라벨링하지 않았으면 None — 0.0으로 쓰면 "못 찾았다"로 오해된다.
        "contradiction_coverage": (
            round(contra_hits / contra_labeled, 3) if contra_labeled else None
        ),
        "unlabeled_in_topk": sum(q["unlabeled_in_topk"] for q in per_query),
        "per_query": per_query,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_retrieval(), ensure_ascii=False, indent=2))
