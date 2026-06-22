"""Bedrock Claude 호출을 담당하는 작은 어댑터.

기본 실행은 기존 mock 모드로 유지한다.
환경변수 ``AGENT_LLM_PROVIDER=bedrock``일 때만 AWS Bedrock Runtime의
Converse API를 호출한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClaudeConfig:
    """Bedrock Claude 실행 설정."""

    provider: str = "mock"
    model_id: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    region_name: str = "us-east-1"
    temperature: float = 0.1
    max_tokens: int = 1800


def load_claude_config() -> ClaudeConfig:
    """환경변수에서 Bedrock Claude 설정을 읽는다."""

    return ClaudeConfig(
        provider=os.getenv("AGENT_LLM_PROVIDER", "mock").lower(),
        model_id=os.getenv(
            "BEDROCK_MODEL_ID",
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
        ),
        region_name=os.getenv(
            "AWS_REGION",
            os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        ),
        temperature=float(os.getenv("BEDROCK_TEMPERATURE", "0.1")),
        max_tokens=int(os.getenv("BEDROCK_MAX_TOKENS", "1800")),
    )


def llm_enabled() -> bool:
    """현재 실행이 Bedrock LLM 모드인지 확인한다."""

    return load_claude_config().provider == "bedrock"


def current_model_name() -> str:
    """AgentRun에 기록할 모델 이름을 반환한다."""

    config = load_claude_config()
    if config.provider == "bedrock":
        return f"bedrock:{config.model_id}"
    return "mock"


def invoke_claude_json(
    *,
    system: str,
    user: str,
    fallback: dict[str, Any],
) -> dict[str, Any]:
<<<<<<< Updated upstream
    """Claude에게 JSON 출력을 요청하고 실패하면 fallback을 반환한다.
=======
    """Invoke Claude and repair one malformed JSON response before failing."""
>>>>>>> Stashed changes

    네트워크, 인증, 권한, JSON 파싱 오류가 있어도 그래프 전체가 깨지지 않게
    기본 mock 결과를 그대로 돌려준다.
    """

    if not llm_enabled():
        return fallback

    config = load_claude_config()

    try:
        import boto3

        client = boto3.client(
            "bedrock-runtime",
            region_name=config.region_name,
        )
        text = _converse_text(
            client=client,
            config=config,
            system=system,
            user=user,
        )
<<<<<<< Updated upstream
        text = _collect_text(response)
        parsed = _parse_json_object(text)
        if not isinstance(parsed, dict):
            return fallback
        return _merge_dicts(fallback, parsed)
=======
        try:
            parsed = _parse_json_object(text)
        except json.JSONDecodeError as first_error:
            parsed = _repair_json_response(
                client=client,
                config=config,
                malformed_text=text,
                parse_error=first_error,
            )
>>>>>>> Stashed changes
    except Exception as exc:
        fallback_with_error = dict(fallback)
        fallback_with_error["llm_error"] = str(exc)
        fallback_with_error["llm_fallback_used"] = True
        return fallback_with_error


def _converse_text(
    *,
    client: Any,
    config: ClaudeConfig,
    system: str,
    user: str,
) -> str:
    response = client.converse(
        modelId=config.model_id,
        system=[{"text": system}],
        messages=[
            {
                "role": "user",
                "content": [{"text": user}],
            }
        ],
        inferenceConfig={
            "temperature": config.temperature,
            "maxTokens": config.max_tokens,
        },
    )
    return _collect_text(response)


def _repair_json_response(
    *,
    client: Any,
    config: ClaudeConfig,
    malformed_text: str,
    parse_error: json.JSONDecodeError,
) -> dict[str, Any]:
    """Ask Claude once to repair syntax without changing the response meaning."""

    repaired_text = _converse_text(
        client=client,
        config=config,
        system=(
            "너는 JSON 문법 교정기다. 입력의 의미와 키·값을 바꾸지 말고 "
            "JSON 문법 오류만 고쳐라. 설명과 마크다운 없이 JSON object 하나만 반환한다."
        ),
        user=(
            f"파싱 오류: {parse_error.msg}, line={parse_error.lineno}, "
            f"column={parse_error.colno}\n\n"
            "다음 응답을 유효한 JSON object로 교정해라:\n"
            f"{malformed_text}"
        ),
    )
    parsed = _parse_json_object(repaired_text)
    if not isinstance(parsed, dict):
        raise RuntimeError("Repaired Bedrock response must be a JSON object.")
    return parsed


def _collect_text(response: dict[str, Any]) -> str:
    """Bedrock Converse 응답에서 text 조각을 모은다."""

    parts = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _parse_json_object(text: str) -> Any:
    """코드블록이 섞인 응답에서도 첫 JSON object를 파싱한다."""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def _merge_dicts(fallback: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """기본 키는 보존하고 Claude가 준 값으로 보강한다."""

    merged = dict(fallback)
    merged.update(parsed)
    merged["llm_provider"] = "bedrock"
    return merged
