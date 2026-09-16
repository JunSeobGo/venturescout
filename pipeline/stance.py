"""stance 산출 — NLI(자연어 추론) 모델로 supports / contradicts / neutral 판정.

ADR-045에서 드러난 구멍을 메운다. 이 프로젝트에는 stance를 **계산하는 코드가
아예 없었다** — reranker는 읽기만 하고, persistence는 "neutral" 하드코딩,
적재 스크립트는 넣지도 않았다. 그 결과 rerank contradiction 축,
`_decide` 규칙2(KILL), contradiction_coverage가 전부 무력이었다.

왜 NLI인가:
    NLI는 (전제, 가설) 쌍을 entailment / contradiction / neutral로 분류한다.
    이 프로젝트의 stance 정의와 **1:1로 대응한다**.
        premise    = 검색된 문서 본문
        hypothesis = H1~H5 가설 문장
        entailment → supports / contradiction → contradicts
    로컬 CPU에서 돌아 Bedrock 비용이 들지 않는다(PatentSBERTa와 같은 방식).

한계(측정 결과와 함께 읽을 것):
    - MNLI 계열 학습 데이터는 일반 문장이다. 특허 청구항·제품 리뷰는 도메인 밖이라
      정확도가 떨어진다. 사람 라벨과 대조해 확인해야 한다.
    - 문서당 1회 추론이 붙어 검색 지연이 늘어난다. STANCE_TAGGING=off로 끌 수 있다.
"""
from __future__ import annotations

import os

# NLI 모델. 3-way(contradiction/entailment/neutral) 분류기여야 한다.
STANCE_MODEL = os.getenv("STANCE_MODEL", "cross-encoder/nli-deberta-v3-base")

# 문서 본문을 자를 길이. NLI는 512토큰 제한이 있고, 앞부분에 요지가 몰려 있다.
STANCE_MAX_CHARS = int(os.getenv("STANCE_MAX_CHARS", "600"))

# 판정 임계값. 최고 확률이 이보다 낮으면 억지로 가르지 않고 neutral로 둔다.
# 근거 없는 contradicts는 KILL 판정을 오발화시키므로 보수적으로 잡는다.
STANCE_MIN_CONFIDENCE = float(os.getenv("STANCE_MIN_CONFIDENCE", "0.5"))

ENABLED = os.getenv("STANCE_TAGGING", "on").lower() not in ("off", "0", "false")

# cross-encoder/nli-* 계열의 출력 순서
_LABELS = ("contradicts", "supports", "neutral")   # contradiction, entailment, neutral


class StanceTagger:
    """문서와 가설을 받아 stance를 판정한다. 모델은 첫 호출에 로드한다."""

    def __init__(self, model_name: str = STANCE_MODEL):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
        return self._model

    def tag(self, hypothesis: str, documents: list[str]) -> list[str]:
        """문서 리스트 각각이 가설을 지지/반박/중립하는지 판정한다.

        전제(premise)가 문서, 가설(hypothesis)이 질문이다. 순서를 바꾸면
        "가설이 문서를 함의하는가"가 되어 의미가 달라진다.
        """
        if not documents:
            return []
        if not ENABLED:
            return ["neutral"] * len(documents)

        import numpy as np

        model = self._load()
        pairs = [(doc[:STANCE_MAX_CHARS], hypothesis) for doc in documents]
        scores = model.predict(pairs, show_progress_bar=False)

        out = []
        for row in np.atleast_2d(scores):
            exp = np.exp(row - np.max(row))
            probs = exp / exp.sum()
            idx = int(np.argmax(probs))
            # 확신이 낮으면 neutral — 근거 없는 contradicts가 KILL을 오발화시킨다
            out.append(_LABELS[idx] if probs[idx] >= STANCE_MIN_CONFIDENCE else "neutral")
        return out


_tagger: StanceTagger | None = None


def get_tagger() -> StanceTagger:
    """프로세스당 하나만 로드한다(모델이 수백 MB)."""
    global _tagger
    if _tagger is None:
        _tagger = StanceTagger()
    return _tagger
