"""하이브리드 융합 방식 — 벡터 항과 키워드 항을 하나의 점수로 합치는 식.

기존 `weighted`는 두 원점수에 0.6/0.4를 곱해 그대로 더했는데, 두 항의 스케일이
달라 명목 가중치가 실제 가중치와 어긋났다(ADR-049). 실측으로 상위 20건에서
`1-cosine`의 폭은 0.17인데 `ts_rank`는 0.73~0.84라, 0.4를 곱한 키워드가 순위
변별의 74~77%를 차지했다 — seed_review만 33%였으니 **코퍼스마다 실효 비율이
달랐다.**

DB를 쓰지 않는다. SQL 조각이 의도한 항을 참조하는지만 고정한다 — 실제 순위
효과는 eval/labelset.py로 잰다.
"""
import pytest

from config import config
from search.hybrid import HybridSearcher


@pytest.fixture
def _restore():
    before = (config.fusion_mode, config.vector_weight, config.keyword_weight, config.rrf_k)
    yield
    (config.fusion_mode, config.vector_weight,
     config.keyword_weight, config.rrf_k) = before


def _expr(mode: str, vw=0.6, kw=0.4) -> str:
    config.fusion_mode, config.vector_weight, config.keyword_weight = mode, vw, kw
    return HybridSearcher._fusion_expr()


# ── weighted (기존 동작) ─────────────────────────────────────────────────────

def test_weighted_uses_raw_scores(_restore):
    expr = _expr("weighted")
    assert "vec_score" in expr and "kw_score" in expr
    assert "0.6" in expr and "0.4" in expr
    assert "rank" not in expr, "weighted는 순위를 쓰지 않는다"


def test_unknown_mode_falls_back_to_weighted(_restore):
    """오타 하나로 검색이 죽는 것보다 기존 동작으로 도는 편이 안전하다."""
    assert _expr("wieghted") == _expr("weighted")


# ── rrf ──────────────────────────────────────────────────────────────────────

def test_rrf_uses_ranks_not_raw_scores(_restore):
    """RRF의 핵심은 원점수를 **버리는** 것이다 — 그래야 스케일 불일치가 사라진다."""
    expr = _expr("rrf")
    assert "vec_rank" in expr and "kw_rank" in expr
    assert "vec_score" not in expr and "kw_score" not in expr


def test_rrf_k_is_configurable(_restore):
    config.rrf_k = 17
    assert "17" in _expr("rrf")


# ── minmax ───────────────────────────────────────────────────────────────────

def test_minmax_normalizes_each_term_over_the_candidate_set(_restore):
    """후보군 전체의 min/max로 편다 — 그래야 0.6/0.4가 의도대로 동작한다."""
    expr = _expr("minmax")
    assert "MIN(vec_score) OVER ()" in expr
    assert "MAX(vec_score) OVER ()" in expr
    assert "0.6" in expr and "0.4" in expr


def test_minmax_guards_zero_range(_restore):
    """모든 값이 같으면 분모가 0이다 — 나눗셈 오류로 검색이 죽으면 안 된다."""
    assert "NULLIF" in _expr("minmax")


# ── 공통 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["weighted", "minmax", "rrf"])
def test_missing_arm_does_not_null_out_the_score(_restore, mode):
    """한쪽 검색에만 걸린 문서는 반대쪽이 NULL이다. NULL이 전파되면 그 문서의
    점수가 통째로 NULL이 되어 합집합에서 조용히 사라진다."""
    assert "COALESCE" in _expr(mode)


def test_modes_produce_different_expressions(_restore):
    exprs = {_expr(m) for m in ("weighted", "minmax", "rrf")}
    assert len(exprs) == 3


def test_default_mode_is_minmax():
    """스케일 불일치가 실측으로 확인된 이상 weighted를 기본값으로 둘 이유가 없다."""
    import importlib

    import config as config_module
    importlib.reload(config_module)
    assert config_module.config.fusion_mode == "minmax"
