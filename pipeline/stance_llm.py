"""LLM 배치 stance 태깅 — documents에 stance를 사전 산출해 채운다.

ADR-046에서 NLI 백엔드를 기각한 뒤의 대안이다. 핵심 설계 두 가지:

1. **문서당 1콜이 아니라 10건을 1콜로 묶는다.** NLI가 문서당 3.3초 걸린 건
   하나씩 처리했기 때문이다. 묶으면 호출 수가 1/10이 되고 지시문 오버헤드도
   분산된다.
2. **검색 시점이 아니라 적재 시점에 한 번만 돈다.** 결과를 documents.stance에
   저장하므로 이후 검색 지연은 0이다. 대신 실행마다 달라지는 가설 대신
   **축(H1~H5) 대표 문장**으로 태깅하는 근사가 된다 — rerank는 절대값이 아니라
   상대 순위만 쓰므로 이 근사로도 반박 근거를 상위로 올리는 효과는 난다.

비용: 890쌍 → 89콜 → 약 $0.61 (1회성). 실행 전 예상치를 출력하고 확인을 받는다.

    python -m pipeline.stance_llm --dry-run     # 비용만 추정
    python -m pipeline.stance_llm --yes         # 실제 실행
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2
import psycopg2.extras

from agents.llm import _price, invoke_claude_json, reset_usage, usage_snapshot
from config import config

# 축별 대표 가설 문장. 실행마다 생성되는 실제 가설의 자리를 대신하는 근사다.
# 영어인 이유: 코퍼스가 영문이고, structuring도 영어로 가설을 만든다.
AXIS_STATEMENTS = {
    "customer_problem": "Customers repeatedly experience this problem and it costs them time or money.",
    "competition": "Existing alternatives leave a meaningful gap that a new entrant could fill.",
    "business_model": "A per-seat subscription pricing model is viable for this market.",
    "technology": "The core technical capability can be built with current technology.",
    "ip": "This technique overlaps with existing patent claims and carries infringement risk.",
}

# 어떤 source_type을 어떤 축으로 태깅할지 — 노드의 검색 스코프와 맞춘다.
SCOPE = {
    "seed_review": ["customer_problem", "business_model"],
    "seed_competitor": ["competition", "business_model"],
    "seed_pricing": ["business_model"],
    "patent": ["technology", "ip"],
}

BATCH = int(os.getenv("STANCE_BATCH", "10"))
DOC_CHARS = int(os.getenv("STANCE_MAX_CHARS", "600"))
VALID = {"supports", "contradicts", "neutral"}


def _conn():
    conn = psycopg2.connect(config.db_dsn, connect_timeout=config.db_connect_timeout)
    return conn


def fetch_targets(conn, source_types: list[str] | None = None) -> list[dict]:
    """태깅 대상 (문서, 축) 쌍을 만든다."""
    sql = (
        "SELECT document_id, source_type, left(clean_text, %s) AS text "
        "FROM documents WHERE clean_text IS NOT NULL"
    )
    params: list = [DOC_CHARS]
    if source_types:
        sql += " AND source_type = ANY(%s)"
        params.append(list(source_types))
    sql += " ORDER BY source_type, document_id"

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {"document_id": str(r["document_id"]), "axis": axis, "text": r["text"]}
        for r in rows
        for axis in SCOPE.get(r["source_type"], [])
    ]


def tag_batch(axis: str, docs: list[dict], model_tier: str = "sonnet") -> dict[str, str]:
    """한 축의 문서 묶음을 1콜로 판정한다."""
    statement = AXIS_STATEMENTS[axis]
    numbered = "\n\n".join(f"[{i}] {d['text']}" for i, d in enumerate(docs, 1))
    system = (
        "너는 근거 분류기다. 각 문서가 주어진 가설을 지지하는지, 반박하는지, "
        "중립인지 판정한다. 논리적 함의가 아니라 **증거로서의 방향**을 본다. "
        "가설을 뒷받침하는 정황이면 supports, 가설이 틀렸음을 시사하면 contradicts, "
        "관련은 있으나 방향이 없으면 neutral이다. "
        "확신이 없으면 neutral로 둔다 — 근거 없는 contradicts는 잘못된 판정을 부른다. "
        "설명 없이 JSON object 하나만 반환한다."
    )
    user = (
        f"가설: {statement}\n\n"
        f"아래 {len(docs)}개 문서 각각을 판정하라.\n"
        '반환 형식: {"1": "supports", "2": "neutral", ...} — 키는 문서 번호 문자열.\n'
        f"값은 supports / contradicts / neutral 중 하나다.\n\n{numbered}"
    )
    out = invoke_claude_json(
        system=system, user=user, model_tier=model_tier, temperature=0.0
    )

    result = {}
    for i, doc in enumerate(docs, 1):
        label = str(out.get(str(i), "neutral")).strip().lower()
        result[doc["document_id"]] = label if label in VALID else "neutral"
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="LLM 배치 stance 태깅 (적재 시점 1회)")
    p.add_argument("--dry-run", action="store_true", help="비용만 추정하고 끝낸다")
    p.add_argument("--yes", action="store_true", help="확인 없이 실행")
    p.add_argument("--limit", type=int, default=0, help="처리할 쌍 수 제한(시험용)")
    p.add_argument(
        "--tier", default="sonnet", choices=["sonnet", "haiku"],
        help="판정에 쓸 모델 티어. haiku는 단가가 1/3이다",
    )
    p.add_argument(
        "--tag-suffix", default="",
        help="meta 키에 붙일 꼬리표(예: _haiku). 비교 실행이 기존 결과를 덮지 않게 한다",
    )
    p.add_argument(
        "--axis", action="append", choices=sorted(AXIS_STATEMENTS),
        help="이 축만 태깅한다(여러 번 지정 가능). 전체를 돌리기 전 소량 검증용",
    )
    p.add_argument(
        "--source-type", action="append", choices=sorted(SCOPE),
        help="이 source_type만 태깅한다(여러 번 지정 가능)",
    )
    args = p.parse_args()

    conn = _conn()
    targets = fetch_targets(conn, source_types=args.source_type)
    if args.axis:
        targets = [t for t in targets if t["axis"] in args.axis]
    if args.limit:
        targets = targets[: args.limit]

    calls = -(-len(targets) // BATCH)
    p_in, p_out = _price(args.tier)
    # 문서 DOC_CHARS자 × BATCH + 지시문 ~400자를 영문 3.6자/토큰으로 환산. 출력은 판정만이라 ~120토큰.
    est = calls * ((DOC_CHARS * BATCH + 400) / 3.6 / 1e6 * p_in + 120 / 1e6 * p_out)
    print(f"태깅 대상 {len(targets)}쌍 → {calls}콜  [{args.tier}]  예상 ${est:.2f}")
    if args.dry_run:
        return
    if not args.yes:
        if input("진행할까? [y/N] ").strip().lower() != "y":
            sys.exit("취소")

    reset_usage()
    by_axis: dict[str, list[dict]] = {}
    for t in targets:
        by_axis.setdefault(t["axis"], []).append(t)

    updates: list[tuple[str, str, str]] = []   # (stance, document_id, axis)
    done = 0
    for axis, docs in by_axis.items():
        for i in range(0, len(docs), BATCH):
            chunk = docs[i : i + BATCH]
            for doc_id, stance in tag_batch(axis, chunk, args.tier).items():
                updates.append((stance, doc_id, axis))
            done += len(chunk)
            snap = usage_snapshot()
            print(f"  {axis:<18} {done}/{len(targets)}  누적 ${snap['cost_usd']:.4f}")

    # 축별 결과를 documents.meta에 넣는다 — 컬럼 추가 없이 스키마를 건드리지 않는다.
    with conn.cursor() as cur:
        for stance, doc_id, axis in updates:
            cur.execute(
                "UPDATE documents SET meta = jsonb_set("
                "  COALESCE(meta,'{}'::jsonb), %s, to_jsonb(%s::text), true) "
                "WHERE document_id = %s",
                ([f"stance_{axis}{args.tag_suffix}"], stance, doc_id),
            )
    conn.commit()

    snap = usage_snapshot()
    print(f"\n완료 — {len(updates)}건 갱신, 실측 ${snap['cost_usd']:.4f} "
          f"({snap['calls']}콜 / in {snap['input_tokens']:,} / out {snap['output_tokens']:,})")


if __name__ == "__main__":
    main()
