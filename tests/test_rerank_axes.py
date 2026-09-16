"""rerank 축이 실제로 순위에 영향을 주는지 — 침묵하는 무력화를 잡는 회귀 테스트.

배경: rerank 가중치 14개 조합을 스윕했더니 **NDCG·P@5·MRR이 전부 동일**했다.
극단값(한 축 1.0, 나머지 0)으로 바꿔도 순위가 한 칸도 안 움직였다.

원인은 4축 중 3축이 **후보 전체에서 상수**라는 것이었다:
  - reliability : source_type마다 하드코딩 (seed 0.6 / patent 0.9).
                  노드가 단일 source_type으로 스코프하므로 한 쿼리 안에서는 상수
  - freshness   : meta.filing_date로 동적 계산은 되지만, 코퍼스가
                  HUPD 2016년 1월 한 달치라 전부 같은 해 → 상수
  - contradiction: stance를 **계산하는 코드가 프로젝트에 없다.**
                  documents 검색 결과에 stance 키 자체가 없어 항상 neutral

상수에 가중치를 곱하면 모든 후보에 같은 값이 더해질 뿐 순서가 안 바뀐다.
결과적으로 순위는 hybrid_score 하나가 100% 결정한다.

이 테스트는 "축이 값이 변할 때 순위를 바꿀 수 있는가"를 고정한다.
DB·LLM을 쓰지 않는다.
"""
import pytest

from search.reranker import ReRanker


def _doc(doc_id: str, hybrid: float, reliability=0.6, freshness=0.7, stance="neutral"):
    return {
        "document_id": doc_id,
        "hybrid_score": hybrid,
        "reliability_score": reliability,
        "freshness_score": freshness,
        "stance": stance,
        "source_type": "seed_review",
        "meta": {},
    }


def _order(ranked):
    return [r["document_id"] for r in ranked]


def test_constant_axes_cannot_change_order():
    """3축이 상수면 가중치를 어떻게 줘도 hybrid 순서 그대로 — 실제 관측된 상태."""
    docs = [_doc("a", 0.9), _doc("b", 0.5), _doc("c", 0.1)]
    orders = set()
    for w in (
        {"relevance_w": 1.0, "reliability_w": 0.0, "freshness_w": 0.0, "contradiction_w": 0.0},
        {"relevance_w": 0.0, "reliability_w": 1.0, "freshness_w": 0.0, "contradiction_w": 0.0},
        {"relevance_w": 0.0, "reliability_w": 0.0, "freshness_w": 0.0, "contradiction_w": 1.0},
    ):
        orders.add(tuple(_order(ReRanker(**w).rerank(docs, top_k=3))))
    assert orders == {("a", "b", "c")}, "상수 축인데 순서가 바뀌었다 — 테스트 전제가 틀렸다"


def test_reliability_changes_order_when_it_actually_varies():
    """reliability가 다르면 순위를 뒤집을 수 있어야 한다(축이 살아있다는 증명)."""
    docs = [_doc("low_rel", 0.9, reliability=0.1), _doc("high_rel", 0.8, reliability=1.0)]
    ranked = ReRanker(relevance_w=0.1, reliability_w=0.9,
                      freshness_w=0.0, contradiction_w=0.0).rerank(docs, top_k=2)
    assert _order(ranked)[0] == "high_rel"


def test_contradiction_axis_promotes_contradicting_evidence():
    """stance가 실려 오면 반박 근거가 상위로 올라가야 한다 — README의 핵심 주장."""
    docs = [_doc("supports", 0.9, stance="supports"),
            _doc("contradicts", 0.8, stance="contradicts")]
    ranked = ReRanker(relevance_w=0.1, reliability_w=0.0,
                      freshness_w=0.0, contradiction_w=0.9).rerank(
        docs, prefer_contradicting=True, top_k=2)
    assert _order(ranked)[0] == "contradicts"


@pytest.mark.xfail(
    reason="stance를 계산하는 코드가 프로젝트에 없다. documents 검색 결과에 "
           "stance 키가 없어 항상 neutral로 떨어지고, contradiction 축이 무력화된다. "
           "이 테스트가 통과하기 시작하면 stance 산출이 구현됐다는 뜻이다.",
    strict=True,
)
def test_document_search_results_carry_stance():
    """검색 결과에 stance가 실려 와야 contradiction 축이 의미를 갖는다."""
    from search.hybrid import HybridSearcher
    import inspect

    src = inspect.getsource(HybridSearcher.search_documents)
    assert "stance" in src, "search_documents가 stance를 반환하지 않는다"
