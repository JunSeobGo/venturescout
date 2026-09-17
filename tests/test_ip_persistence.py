"""IP 중첩 후보 적재 — 시그니처 기능의 산출물이 DB에 남는가.

`ip_overlap_candidates`에 INSERT하는 `create_ip_overlap_candidates`의 호출처는
`find_ip_overlap_candidates` 하나뿐인데, 그래프는 그쪽이 아니라
`retrieval.tools.vector_search`를 쓴다. 그래서 후보가 메모리로는 에이전트에
도달하지만 **테이블에는 한 행도 안 쌓였다** — "무엇이 겹쳤는가"를 사후에 확인할
방법이 없었다(ADR §5 open).

DB를 쓰지 않는다. 적재 경로가 불리는지, 실패해도 분석이 살아남는지를 고정한다.
"""
import pytest

from pipeline import persistence
from retrieval import tools


def _row(i: int) -> dict:
    return {
        "limitation_id": f"lim-{i}",
        "document_id": f"doc-{i}",
        "patent_id": f"pat-{i}",
        "normalized_text": f"limitation text {i}",
        "lexical_score": 0.3,
        "similarity_score": 0.7,
        "hybrid_score": 0.9 - i * 0.1,
    }


@pytest.fixture
def _fake_search(monkeypatch):
    """claim_limitations 검색과 rerank를 가짜로 바꿔 DB 없이 vector_search를 돈다."""
    rows = [_row(1), _row(2), _row(3)]

    class _S:
        def search_claim_limitations(self, **kwargs):
            return [dict(r) for r in rows]

    class _R:
        def rerank(self, items, **kwargs):
            return items

    monkeypatch.setattr(tools, "_get_engines", lambda: (_S(), _R()))
    return rows


@pytest.fixture
def _calls(monkeypatch):
    seen = []
    monkeypatch.setattr(tools, "persist_ip_overlap_candidates",
                        lambda **kw: seen.append(kw) or ["c1"])
    return seen


# ── 배선 ─────────────────────────────────────────────────────────────────────

def test_vector_search_persists_candidates(_fake_search, _calls):
    tools.vector_search(["cross-border settlement"], job_id="job-1",
                        hypothesis_id="H5", k=3)
    assert len(_calls) == 1, "후보를 만들고도 적재를 부르지 않았다"
    assert _calls[0]["job_id"] == "job-1"
    assert _calls[0]["hypothesis_id"] == "H5"


def test_persisted_rows_are_the_ones_the_agent_saw(_fake_search, _calls):
    """감사 기록의 목적상, 에이전트가 실제로 본 후보와 같아야 한다."""
    out = tools.vector_search(["x"], job_id="job-1", hypothesis_id="H5", k=2)
    persisted = [r["limitation_id"] for r in _calls[0]["rows"]]
    assert persisted == [c.limitation_id for c in out]
    assert len(persisted) == 2, "k로 자른 뒤의 목록이 남아야 한다"


def test_plan_technical_element_is_the_first_element(_fake_search, _calls):
    tools.vector_search(["첫 요소", "둘째"], job_id="job-1", hypothesis_id="H5")
    assert _calls[0]["plan_technical_element"] == "첫 요소"


def test_persist_failure_does_not_break_analysis(_fake_search, monkeypatch):
    """적재는 부가 기능이다 — 실패해도 IP 노드가 후보를 못 받으면 안 된다."""
    def _boom(**kwargs):
        raise RuntimeError("DB 없음")

    monkeypatch.setattr(persistence, "get_connection", _boom)
    out = tools.vector_search(["x"], job_id="job-1", hypothesis_id="H5", k=3)
    assert len(out) == 3


# ── 가드 ─────────────────────────────────────────────────────────────────────

def test_no_job_id_skips_persistence():
    """job_id가 없으면 FK를 만족할 수 없다 — 시도하지 않는다."""
    assert persistence.persist_ip_overlap_candidates(
        job_id="", hypothesis_id="H5", plan_technical_element="x", rows=[_row(1)]
    ) == []


def test_empty_rows_skips_persistence():
    assert persistence.persist_ip_overlap_candidates(
        job_id="job-1", hypothesis_id="H5", plan_technical_element="x", rows=[]
    ) == []


def test_unresolvable_hypothesis_skips_rather_than_raising(monkeypatch):
    """structuring이 hypotheses 행을 안 남겼으면 FK가 깨진다. 죽지 말고 건너뛴다."""
    class _Conn:
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(persistence, "get_connection", lambda: _Conn())
    monkeypatch.setattr(persistence, "_resolve_hypothesis_uuid",
                        lambda conn, job_id, hyp: None)
    assert persistence.persist_ip_overlap_candidates(
        job_id="job-1", hypothesis_id="H5", plan_technical_element="x", rows=[_row(1)]
    ) == []
