"""stance 산출 로직 검증 — 모델 없이 판정 규칙만 고정.

실제 NLI 추론은 모델 다운로드가 필요하므로 여기서는 하지 않는다.
대신 "확률을 어떻게 stance로 바꾸는가"의 규칙을 고정한다 —
이 부분이 KILL 오발화와 직결되기 때문이다.
"""
import numpy as np
import pytest

from pipeline import stance as st


class _FakeModel:
    """CrossEncoder 대역. predict가 (n, 3) 로짓을 돌려준다."""

    def __init__(self, rows):
        self.rows = rows
        self.seen = None

    def predict(self, pairs, show_progress_bar=False):
        self.seen = pairs
        return np.array(self.rows)


def _tagger(rows, monkeypatch, **env):
    for key, val in env.items():
        monkeypatch.setattr(st, key, val)
    t = st.StanceTagger()
    t._model = _FakeModel(rows)
    return t


def test_label_order_matches_model_output(monkeypatch):
    """cross-encoder/nli-* 출력은 (contradiction, entailment, neutral) 순서다."""
    assert st._LABELS == ("contradicts", "supports", "neutral")


def test_high_confidence_entailment_is_supports(monkeypatch):
    t = _tagger([[0.0, 9.0, 0.0]], monkeypatch)
    assert t.tag("가설", ["문서"]) == ["supports"]


def test_high_confidence_contradiction_is_contradicts(monkeypatch):
    t = _tagger([[9.0, 0.0, 0.0]], monkeypatch)
    assert t.tag("가설", ["문서"]) == ["contradicts"]


def test_low_confidence_falls_back_to_neutral(monkeypatch):
    """확신이 낮으면 억지로 가르지 않는다 — 근거 없는 contradicts는 KILL을 오발화시킨다."""
    t = _tagger([[1.0, 0.9, 0.8]], monkeypatch, STANCE_MIN_CONFIDENCE=0.9)
    assert t.tag("가설", ["문서"]) == ["neutral"]


def test_threshold_zero_never_falls_back(monkeypatch):
    t = _tagger([[1.0, 0.9, 0.8]], monkeypatch, STANCE_MIN_CONFIDENCE=0.0)
    assert t.tag("가설", ["문서"]) == ["contradicts"]


def test_premise_is_document_hypothesis_is_query(monkeypatch):
    """순서가 바뀌면 '가설이 문서를 함의하는가'가 되어 의미가 달라진다."""
    t = _tagger([[0.0, 9.0, 0.0]], monkeypatch)
    t.tag("이것은 가설이다", ["이것은 문서다"])
    assert t._model.seen == [("이것은 문서다", "이것은 가설이다")]


def test_document_is_truncated(monkeypatch):
    t = _tagger([[0.0, 9.0, 0.0]], monkeypatch, STANCE_MAX_CHARS=10)
    t.tag("가설", ["가" * 100])
    assert len(t._model.seen[0][0]) == 10


def test_disabled_returns_all_neutral_without_loading_model(monkeypatch):
    """STANCE_TAGGING=off면 모델을 건드리지 않고 neutral을 돌려준다."""
    monkeypatch.setattr(st, "ENABLED", False)
    t = st.StanceTagger()          # _model을 채우지 않는다 — 로드하면 여기서 터진다
    assert t.tag("가설", ["a", "b", "c"]) == ["neutral"] * 3


def test_empty_documents_returns_empty(monkeypatch):
    monkeypatch.setattr(st, "ENABLED", True)
    assert st.StanceTagger().tag("가설", []) == []


def test_batch_keeps_order(monkeypatch):
    t = _tagger([[9, 0, 0], [0, 9, 0], [0, 0, 9]], monkeypatch)
    assert t.tag("가설", ["d1", "d2", "d3"]) == ["contradicts", "supports", "neutral"]
