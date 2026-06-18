"""상세 노드가 실제 State 데이터를 읽기 위한 내부 보조 함수.

공통 DB/State 계약은 변경하지 않고, 기존 노드의 mock 하드코딩만 입력 기반 값으로
교체하기 위해 사용한다.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from agents.llm import invoke_claude_json


def as_dict(value: Any) -> dict[str, Any]:
    """dict와 Pydantic 모델을 동일한 형태로 읽는다."""

    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def _labeled_value(raw_input: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(
            rf"(?im)^\s*{re.escape(label)}\s*[:：]\s*(.+?)\s*$",
            raw_input,
        )
        if match:
            return match.group(1).strip()
    return None


def _split_values(value: str | None) -> list[str]:
    if not value:
        return []
    values = [
        item.strip()
        for item in re.split(r"[,;/|\n]+", value)
        if item.strip()
    ]
    return list(dict.fromkeys(values))


def _first_line(raw_input: str) -> str:
    for line in raw_input.splitlines():
        cleaned = line.strip(" \t#-*")
        if cleaned:
            return cleaned[:300]
    return "제목 미확인 아이디어"


def structure_raw_input(raw_input: str) -> dict[str, Any]:
    """원문을 기존 Structuring State 필드 형태로 변환한다."""

    fallback = {
        "title": _labeled_value(raw_input, ("제목", "아이디어명", "title"))
        or _first_line(raw_input),
        "idea_type": _labeled_value(
            raw_input,
            ("아이디어 유형", "서비스 유형", "idea type"),
        ),
        "target_customer": _labeled_value(
            raw_input,
            ("타깃 고객", "목표 고객", "고객", "target customer"),
        ),
        "problem_statement": _labeled_value(
            raw_input,
            ("문제", "문제 정의", "problem"),
        ),
        "solution_summary": _labeled_value(
            raw_input,
            ("해결책", "솔루션", "solution"),
        ),
        "business_model_hint": _labeled_value(
            raw_input,
            ("비즈니스 모델", "수익 모델", "business model"),
        ),
        "technical_elements": _split_values(
            _labeled_value(
                raw_input,
                ("기술 요소", "핵심 기술", "technical elements"),
            )
        ),
        "patent_keywords": _split_values(
            _labeled_value(
                raw_input,
                ("특허 키워드", "검색 키워드", "patent keywords"),
            )
        ),
    }

    parsed = invoke_claude_json(
        system=(
            "사용자의 사업 아이디어 원문을 구조화한다. 원문에 없는 내용을 단정하지 "
            "말고 요청된 JSON 키만 반환한다."
        ),
        user=(
            f"{raw_input}\n\n"
            "JSON 키: title, idea_type, target_customer, problem_statement, "
            "solution_summary, business_model_hint, technical_elements, patent_keywords"
        ),
        fallback=fallback,
    )
    return {
        key: parsed.get(key) if parsed.get(key) not in (None, "", []) else value
        for key, value in fallback.items()
    }


def state_evidence(
    state: dict[str, Any],
    hypothesis_id: str,
    fallback: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """State의 evidence_items를 우선하고 없으면 기존 repository 결과를 쓴다."""

    accepted_ids = {hypothesis_id}
    for value in state.get("hypotheses", []):
        hypothesis = as_dict(value)
        if hypothesis.get("code") == hypothesis_id:
            accepted_ids.add(str(hypothesis.get("hypothesis_id")))

    stored = state.get("evidence_items") or {}
    values = stored.values() if isinstance(stored, dict) else stored
    evidence = [
        item
        for value in values
        if (item := as_dict(value))
        and str(item.get("hypothesis_id")) in accepted_ids
    ]
    return evidence or list(fallback)


def state_ip_candidates(
    state: dict[str, Any],
    fallback: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """State의 IP 후보를 우선하고 없으면 기존 repository 결과를 쓴다."""

    accepted_ids = {"H5"}
    for value in state.get("hypotheses", []):
        hypothesis = as_dict(value)
        if hypothesis.get("code") == "H5":
            accepted_ids.add(str(hypothesis.get("hypothesis_id")))

    candidates = [
        item
        for value in (state.get("ip_overlap_candidates") or [])
        if (item := as_dict(value))
        and str(item.get("hypothesis_id")) in accepted_ids
    ]
    return candidates or list(fallback)
