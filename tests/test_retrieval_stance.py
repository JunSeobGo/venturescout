"""검색 결과에 stance가 실려 오는지 — contradiction 축이 살아 있다는 증명.

적재 시점에 태깅한 값은 `documents.meta`에 담겨 검색 결과와 함께 나오는데, 읽는
쪽(reranker, EvidenceItem)은 최상위 `stance` 키를 본다. 그 사이가 이어져 있지 않아
항상 neutral로 떨어졌고 contradiction 축이 전 문서 0.5 고정이었다(ADR-045 → 047).

DB·LLM을 쓰지 않는다 — `_attach_stance`는 순수 함수이고, retrieve()는 검색기를
가짜로 바꿔 넣어 검증한다.
"""
import pytest

from retrieval import tools


def _row(doc_id: str, meta: dict | None, **extra) -> dict:
    return {
        "document_id": doc_id,
        "source_type": "seed_review",
        "clean_text": f"본문 {doc_id}",
        "meta": meta,
        "hybrid_score": 0.5,
        "reliability_score": 0.5,
        **extra,
    }


# ── _attach_stance ───────────────────────────────────────────────────────────

def test_lifts_axis_stance_from_meta():
    rows = [_row("d1", {"stance_business_model": "contradicts"})]
    tools._attach_stance(rows, "H3")
    assert rows[0]["stance"] == "contradicts"


def test_reads_the_axis_matching_the_hypothesis():
    """같은 문서라도 어느 가설로 검색하느냐에 따라 stance가 다르다."""
    meta = {"stance_business_model": "contradicts", "stance_customer_problem": "supports"}
    rows = [_row("d1", dict(meta))]
    tools._attach_stance(rows, "H1")
    assert rows[0]["stance"] == "supports"
    rows = [_row("d1", dict(meta))]
    tools._attach_stance(rows, "H3")
    assert rows[0]["stance"] == "contradicts"


def test_axis_name_passed_directly_also_works():
    """eval/labelset.py는 H3이 아니라 'business_model'을 그대로 넘긴다."""
    rows = [_row("d1", {"stance_business_model": "contradicts"})]
    tools._attach_stance(rows, "business_model")
    assert rows[0]["stance"] == "contradicts"


@pytest.mark.parametrize("meta", [None, {}, {"stance_technology": "contradicts"}, "문자열"])
def test_untagged_falls_back_to_neutral(meta):
    """태깅 안 된 축·문서는 neutral — 없는 판단을 지어내지 않는다."""
    rows = [_row("d1", meta)]
    tools._attach_stance(rows, "H3")
    assert rows[0]["stance"] == "neutral"


def test_null_stance_in_meta_is_neutral_not_none():
    """meta에 키는 있는데 값이 null이면 Stance 리터럴을 깨뜨리면 안 된다."""
    rows = [_row("d1", {"stance_business_model": None})]
    tools._attach_stance(rows, "H3")
    assert rows[0]["stance"] == "neutral"


# ── retrieve() 통합 — 배선이 실제로 이어졌는가 ────────────────────────────────

class _FakeSearcher:
    def __init__(self, rows):
        self.rows = rows

    def search_documents(self, **kwargs):
        return [dict(r) for r in self.rows]


@pytest.fixture
def _fake_engines(monkeypatch):
    from search.reranker import ReRanker

    def _install(rows):
        monkeypatch.setattr(
            tools, "_get_engines",
            lambda: (_FakeSearcher(rows), ReRanker(
                relevance_w=0.1, reliability_w=0.0,
                freshness_w=0.0, contradiction_w=0.9,
            )),
        )
    return _install


def test_evidence_items_carry_real_stance(_fake_engines):
    _fake_engines([
        _row("d1", {"stance_business_model": "contradicts"}),
        _row("d2", {"stance_business_model": "supports"}),
    ])
    items = tools.retrieve("H3", "질의", k=2)
    assert {i.document_id: i.stance for i in items} == {
        "d1": "contradicts", "d2": "supports",
    }


def test_contradicting_evidence_is_promoted(_fake_engines):
    """관련도가 더 낮아도 반박 근거가 위로 올라와야 한다 — README의 핵심 주장."""
    _fake_engines([
        _row("supports_hi", {"stance_business_model": "supports"}, hybrid_score=0.9),
        _row("contradicts_lo", {"stance_business_model": "contradicts"}, hybrid_score=0.1),
    ])
    items = tools.retrieve("H3", "질의", k=2)
    assert items[0].document_id == "contradicts_lo"


def test_untagged_corpus_keeps_original_order(_fake_engines):
    """stance가 없으면 contradiction 축은 상수라 순위를 바꾸지 않는다(회귀 방지)."""
    _fake_engines([
        _row("hi", None, hybrid_score=0.9),
        _row("lo", None, hybrid_score=0.1),
    ])
    items = tools.retrieve("H3", "질의", k=2)
    assert [i.document_id for i in items] == ["hi", "lo"]
