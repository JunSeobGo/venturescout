"""Strict Amazon Bedrock Claude adapter for live VentureScout runs."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Literal

from dotenv import load_dotenv

load_dotenv()


# ── 토큰 사용량 집계 + 비용 산정 ────────────────────────────────────────────
# Bedrock converse 응답의 usage(input/output 토큰)를 캡처해 누적한다.
# ADR-029/035의 cost_usd=None TODO를 닫기 위함 — 추정이 아니라 실측 토큰 기반.
#
# 단가는 **티어마다 다르다**(1M 토큰 기준, claude-api 레퍼런스 2026-06):
#   sonnet (Sonnet 4.6)  입력 $3 / 출력 $15
#   haiku  (Haiku 4.5)   입력 $1 / 출력 $5    — 정확히 1/3
# 그래서 누적도 티어별로 나눠 센다. 한 덩어리로 세면 haiku 호출까지 sonnet 단가로
# 계산돼 비용이 3배로 잡히고, 그 숫자로 상한을 걸면 멀쩡한 실행이 막힌다.
# 리전·계약별로 다르면 .env로 덮어쓴다.
PRICE_INPUT_PER_MTOK = float(os.getenv("BEDROCK_PRICE_INPUT_PER_MTOK", "3.0"))
PRICE_OUTPUT_PER_MTOK = float(os.getenv("BEDROCK_PRICE_OUTPUT_PER_MTOK", "15.0"))
HAIKU_PRICE_INPUT_PER_MTOK = float(os.getenv("BEDROCK_HAIKU_PRICE_INPUT_PER_MTOK", "1.0"))
HAIKU_PRICE_OUTPUT_PER_MTOK = float(os.getenv("BEDROCK_HAIKU_PRICE_OUTPUT_PER_MTOK", "5.0"))

_USAGE_LOCK = threading.Lock()  # 분석 5노드가 병렬 스레드라 누적은 lock으로 보호
_USAGE: dict[str, dict[str, int]] = {}   # tier -> {input_tokens, output_tokens, calls}


def _price(model_tier: str) -> tuple[float, float]:
    """(입력, 출력) 단가. 모르는 티어는 비싼 쪽(sonnet)으로 본다 — 과소평가보다 안전하다."""
    if model_tier == "haiku":
        return HAIKU_PRICE_INPUT_PER_MTOK, HAIKU_PRICE_OUTPUT_PER_MTOK
    return PRICE_INPUT_PER_MTOK, PRICE_OUTPUT_PER_MTOK


def _record_usage(
    input_tokens: int,
    output_tokens: int,
    model_tier: str = "sonnet",
) -> None:
    with _USAGE_LOCK:
        bucket = _USAGE.setdefault(
            model_tier, {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        )
        bucket["input_tokens"] += int(input_tokens or 0)
        bucket["output_tokens"] += int(output_tokens or 0)
        bucket["calls"] += 1


def _totals_locked() -> tuple[int, int, int, float]:
    """(입력, 출력, 호출 수, 비용). 호출자가 _USAGE_LOCK을 잡고 있어야 한다."""
    in_tok = out_tok = calls = 0
    cost = 0.0
    for tier, bucket in _USAGE.items():
        p_in, p_out = _price(tier)
        in_tok += bucket["input_tokens"]
        out_tok += bucket["output_tokens"]
        calls += bucket["calls"]
        cost += (
            bucket["input_tokens"] / 1_000_000 * p_in
            + bucket["output_tokens"] / 1_000_000 * p_out
        )
    return in_tok, out_tok, calls, cost


def reset_usage() -> None:
    """잡(또는 평가 1회) 시작 전에 호출해 누적을 0으로 되돌린다."""
    with _USAGE_LOCK:
        _USAGE.clear()


class BudgetExceeded(RuntimeError):
    """잡 하나가 정해둔 비용/호출 상한을 넘었을 때. 다음 LLM 호출을 막는다."""


# ── 실행 상한 (guardrail) ──────────────────────────────────────────────────
# 지금까지는 usage를 **재기만 하고 막지는 않았다**. 루프 버그나 재시도 폭주가
# 나면 그대로 과금된다. AWS 계정이 개인 명의로 넘어와 실제 위험이 됐으므로
# 호출 관문(invoke_claude_json)에서 선제적으로 차단한다.
#
# 분석 1건 실측이 약 $0.40(7~8콜)이므로 기본값은 그 3배 정도로 잡아 정상 실행은
# 막지 않으면서 폭주만 잡는다. .env로 조정한다.
MAX_COST_USD_PER_JOB = float(os.getenv("MAX_COST_USD_PER_JOB", "1.5"))
MAX_LLM_CALLS_PER_JOB = int(os.getenv("MAX_LLM_CALLS_PER_JOB", "40"))


def _check_budget() -> None:
    """호출 직전 상한 확인. 넘었으면 BudgetExceeded를 올려 더 못 쓰게 한다."""
    with _USAGE_LOCK:
        _, _, calls, cost = _totals_locked()
    if MAX_LLM_CALLS_PER_JOB and calls >= MAX_LLM_CALLS_PER_JOB:
        raise BudgetExceeded(
            f"LLM 호출 상한 초과: {calls}회 >= {MAX_LLM_CALLS_PER_JOB}회. "
            "MAX_LLM_CALLS_PER_JOB으로 조정한다."
        )
    if MAX_COST_USD_PER_JOB and cost >= MAX_COST_USD_PER_JOB:
        raise BudgetExceeded(
            f"비용 상한 초과: ${cost:.4f} >= ${MAX_COST_USD_PER_JOB}. "
            "MAX_COST_USD_PER_JOB으로 조정한다."
        )


def usage_snapshot() -> dict:
    """현재까지 누적된 토큰과 그로부터 계산한 USD 비용을 반환한다.

    최상위 키는 **전 티어 합계**다(기존 소비자 호환 — app/api.py, eval/harness.py).
    티어를 섞어 돌린 실행에서 어느 쪽이 얼마를 썼는지는 by_tier로 본다.
    """
    with _USAGE_LOCK:
        in_tok, out_tok, calls, cost = _totals_locked()
        by_tier = {tier: dict(bucket) for tier, bucket in _USAGE.items()}
    for tier, bucket in by_tier.items():
        p_in, p_out = _price(tier)
        bucket["cost_usd"] = round(
            bucket["input_tokens"] / 1_000_000 * p_in
            + bucket["output_tokens"] / 1_000_000 * p_out,
            6,
        )
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "calls": calls,
        "cost_usd": round(cost, 6),
        "by_tier": by_tier,
    }


# ── LangSmith 트레이싱 (선택) ───────────────────────────────────────────────
# langsmith 미설치 또는 LANGCHAIN_TRACING_V2 미설정 시 완전 no-op.
# 설정 시 각 Bedrock 호출이 run_type="llm" 스팬으로 잡히고 토큰 usage가 붙어
# LangGraph 노드 트리와 함께 smith.langchain.com 대시보드에서 비용까지 추적된다.
try:
    from langsmith import traceable as _ls_traceable
    from langsmith.run_helpers import get_current_run_tree as _ls_run_tree
    _LANGSMITH = True
except Exception:  # langsmith 미설치
    _LANGSMITH = False

    def _ls_traceable(*dargs, **dkwargs):
        # @_ls_traceable 와 @_ls_traceable(...) 둘 다 지원하는 no-op
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]

        def _deco(fn):
            return fn

        return _deco


def _ls_model_name(model_id: str) -> str:
    """Bedrock 모델 id를 LangSmith 가격표가 인식하는 1P 모델명으로 정규화한다.

    예) 'jp.anthropic.claude-sonnet-4-6' / 'anthropic.claude-sonnet-4-6'
        → 'claude-sonnet-4-6'
    리전 추론 프로파일 프리픽스(jp./us./eu./apac. 등)와 'anthropic.' 프로바이더
    프리픽스를 떼어낸다. LangSmith는 이 이름으로 단가를 찾아 비용을 계산한다.
    """
    parts = (model_id or "").split(".")
    if "anthropic" in parts:
        return ".".join(parts[parts.index("anthropic") + 1 :]) or model_id
    return model_id


def _report_usage_to_langsmith(model_id: str, input_tokens: int, output_tokens: int) -> None:
    """현재 트레이싱 중이면 run에 토큰/모델 메타를 붙인다(비활성/실패 시 무시).

    토큰은 RunTree.outputs의 usage_metadata 키로 넣어야 LangSmith가 집계·과금한다.
    (langsmith 0.8.x RunTree에는 usage_metadata 필드가 없어 직접 대입은 무시된다.)
    비용은 extra.metadata의 ls_model_name을 가격표와 매칭해 계산되므로 1P 모델명을 보낸다.
    """
    if not _LANGSMITH:
        return
    try:
        rt = _ls_run_tree()
        if rt is None:
            return

        in_tok, out_tok = int(input_tokens or 0), int(output_tokens or 0)
        usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        }
        rt.outputs = {**(rt.outputs or {}), "usage_metadata": usage}

        rt.extra = rt.extra or {}
        rt.extra.setdefault("metadata", {}).update(
            {"ls_provider": "anthropic", "ls_model_name": _ls_model_name(model_id)}
        )
    except Exception:
        pass  # 트레이싱 실패가 분석을 깨뜨리지 않게


ModelTier = Literal["sonnet", "haiku"]

DEFAULT_MODEL_IDS: dict[ModelTier, str] = {
    "sonnet": "anthropic.claude-sonnet-4-6",
    "haiku": "anthropic.claude-haiku-4-5-20251001-v1:0",
}

# 티어별 모델 id 환경변수. 티어를 늘릴 때 여기와 DEFAULT_MODEL_IDS만 추가하면 된다.
MODEL_ID_ENV: dict[ModelTier, str] = {
    "sonnet": "BEDROCK_SONNET_MODEL_ID",
    "haiku": "BEDROCK_HAIKU_MODEL_ID",
}

# 기본은 **전부 sonnet이다.** 어떤 노드를 haiku로 내릴지는 추측이 아니라 측정으로
# 정한다(ADR-046에서 NLI를 정확도 33%로 기각한 것과 같은 기준). 실험할 때는
# 코드를 고치지 말고 AGENT_TIER_OVERRIDES로 바꾼다 — 실험 설정이 커밋에 섞이면
# "무엇을 재고 있었는지"가 흐려진다.
MODEL_TIER_BY_AGENT: dict[str, ModelTier] = {
    "structuring": "sonnet",
    "market": "sonnet",
    "competitor": "sonnet",
    "bm": "sonnet",
    "tech": "sonnet",
    "ip": "sonnet",
    "critic": "sonnet",
}


@dataclass(frozen=True)
class ClaudeConfig:
    provider: str = "bedrock"
    model_tier: ModelTier = "sonnet"
    model_id: str = DEFAULT_MODEL_IDS["sonnet"]
    region_name: str = "us-east-1"
    temperature: float = 0.1
    max_tokens: int = 1800


def _tier_overrides() -> dict[str, ModelTier]:
    """AGENT_TIER_OVERRIDES="market=haiku,critic=sonnet" 형식을 파싱한다.

    A/B 실험용 스위치다. 모르는 티어 이름은 조용히 버린다 — 오타 때문에 실행이
    통째로 죽는 것보다 기본값(sonnet)으로 도는 편이 안전하다.
    """
    raw = os.getenv("AGENT_TIER_OVERRIDES", "").strip()
    if not raw:
        return {}
    out: dict[str, ModelTier] = {}
    for part in raw.split(","):
        name, _, tier = part.partition("=")
        name, tier = name.strip(), tier.strip()
        if name and tier in DEFAULT_MODEL_IDS:
            out[name] = tier  # type: ignore[assignment]
    return out


def model_tier_for_agent(agent_name: str) -> ModelTier:
    override = _tier_overrides().get(agent_name)
    if override:
        return override
    return MODEL_TIER_BY_AGENT.get(agent_name, "sonnet")


def _model_id_for_tier(model_tier: ModelTier) -> str:
    """티어에 해당하는 모델 id.

    이전 구현은 인자를 받고도 무시한 채 항상 BEDROCK_SONNET_MODEL_ID를 돌려줬다.
    티어가 하나뿐일 땐 티가 안 났지만, haiku를 추가하면 **에러 없이 sonnet으로
    가면서 비용도 품질도 측정이 어긋난다.** 티어별 환경변수를 먼저 본다.
    """
    explicit = os.getenv(MODEL_ID_ENV[model_tier])
    if explicit:
        return explicit
    # BEDROCK_MODEL_ID는 티어 구분이 없던 시절의 공통 덮어쓰기다. 그대로 두면
    # 이게 설정된 환경에서 haiku가 sonnet으로 새므로 sonnet에만 적용한다.
    if model_tier == "sonnet":
        legacy = os.getenv("BEDROCK_MODEL_ID")
        if legacy:
            return legacy
    return DEFAULT_MODEL_IDS[model_tier]


def load_claude_config(model_tier: ModelTier = "sonnet") -> ClaudeConfig:
    return ClaudeConfig(
        provider=os.getenv("AGENT_LLM_PROVIDER", "bedrock").lower(),
        model_tier=model_tier,
        model_id=_model_id_for_tier(model_tier),
        region_name=os.getenv(
            "AWS_REGION",
            os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        ),
        temperature=float(os.getenv("BEDROCK_TEMPERATURE", "0.1")),
        max_tokens=int(os.getenv("BEDROCK_MAX_TOKENS", "1800")),
    )


def _require_bedrock(config: ClaudeConfig) -> None:
    if config.provider != "bedrock":
        raise RuntimeError(
            "AGENT_LLM_PROVIDER must be 'bedrock'. "
            "Live analysis does not allow fixed LLM output."
        )


def current_model_name(agent_name: str = "structuring") -> str:
    config = load_claude_config(model_tier_for_agent(agent_name))
    _require_bedrock(config)
    return f"bedrock:{config.model_tier}:{config.model_id}"


def validate_bedrock_environment(model_tier: ModelTier = "sonnet") -> dict[str, str]:
    config = load_claude_config(model_tier)
    _require_bedrock(config)

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is not installed. Install requirements.txt first."
        ) from exc

    session = boto3.Session(region_name=config.region_name)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "AWS credentials were not found. Configure AWS_PROFILE or "
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
        )

    return {
        "provider": config.provider,
        "region": config.region_name,
        "model_tier": config.model_tier,
        "model_id": config.model_id,
        "credential_method": getattr(credentials, "method", "unknown"),
    }


@_ls_traceable(run_type="llm", name="bedrock_converse")
def invoke_claude_json(
    *,
    system: str,
    user: str,
    model_tier: ModelTier = "sonnet",
    temperature: float | None = None,
) -> dict[str, Any]:
    """Invoke Claude and fail the run if Bedrock or JSON parsing fails.

    temperature를 주면 config 기본값(BEDROCK_TEMPERATURE)을 호출 단위로 덮어쓴다.
    구조화처럼 결정성이 중요한 호출은 0을 넘겨 검색 쿼리 변동을 줄인다.
    """

    _check_budget()          # 호출 전에 상한부터 확인한다(재기만 하지 않고 막는다)
    config = load_claude_config(model_tier)
    _require_bedrock(config)
    call_temperature = config.temperature if temperature is None else temperature

    try:
        import boto3
        from botocore.config import Config as _BotoConfig

        client = boto3.client(
            "bedrock-runtime",
            region_name=config.region_name,
            config=_BotoConfig(
                read_timeout=int(os.getenv("BEDROCK_READ_TIMEOUT", "300")),
                connect_timeout=10,
                retries={"max_attempts": 2},
            ),
        )
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
                "temperature": call_temperature,
                "maxTokens": config.max_tokens,
            },
        )
        # 토큰 사용량 캡처(실측 비용 + LangSmith 트레이싱). 파싱 실패와 무관하게
        # 호출 자체는 과금되므로 parse 이전에 기록한다.
        _usage = response.get("usage", {}) or {}
        _in_tok, _out_tok = _usage.get("inputTokens", 0), _usage.get("outputTokens", 0)
        _record_usage(_in_tok, _out_tok, model_tier)
        _report_usage_to_langsmith(config.model_id, _in_tok, _out_tok)
        parsed = _parse_json_object(_collect_text(response))
    except Exception as exc:
        raise RuntimeError(
            f"Bedrock Claude invocation failed: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("Bedrock Claude response must be a JSON object.")

    parsed["llm_provider"] = "bedrock"
    parsed["llm_model_id"] = config.model_id
    parsed["llm_succeeded"] = True
    return parsed


def _collect_text(response: dict[str, Any]) -> str:
    parts = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _parse_json_object(text: str) -> Any:
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
