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
# 축별 판정 기준. 초판은 "확신이 없으면 neutral로 둔다"는 한 줄이 전부였는데,
# 그 지시를 Sonnet이 문자 그대로 따르면서 90건 중 contradicts를 8건만 냈다
# (Haiku는 49건). 불일치 44건 중 41건이 contradicts→neutral 한 방향이었다.
# 모델 능력 차이가 아니라 **프롬프트가 neutral로 쏠려 있었던 것**이라, 축마다
# 무엇이 반박/지지에 해당하는지를 명시해 판단 기준을 프롬프트 밖으로 꺼낸다.
AXIS_CRITERIA = {
    "customer_problem": {
        "supports": "반복되는 불편, 시간·비용 손실, 우회 작업(workaround)을 언급한다",
        "contradicts": "그 문제가 없거나 사소하다, 기존 방식으로 충분하다고 말한다",
    },
    "competition": {
        "supports": "기존 대안의 결함·공백·미충족 요구를 드러낸다",
        "contradicts": "기존 대안이 이미 충분하다거나 시장이 포화라고 말한다",
    },
    "business_model": {
        "supports": "가격이 값어치를 한다, 좌석당 구독이 무리 없다고 평가한다",
        "contradicts": "가격 수준·가격 구조의 복잡성·계약 조건에 불만을 제기하거나, "
                       "상위 티어에서만 기능을 열어줘 하위 플랜이 쓸 수 없다고 지적한다",
    },
    "technology": {
        "supports": "해당 역량이 이미 구현·구동된 사례를 보여준다",
        "contradicts": "미해결 기술 난제나 구현 한계를 지적한다",
    },
    "ip": {
        "supports": "청구항이 해당 기법을 포괄해 침해 위험을 시사한다",
        "contradicts": "해당 기법이 청구 범위 밖이거나 이미 공지기술임을 시사한다",
    },
}

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


def tag_batch(
    axis: str, docs: list[dict], model_tier: str = "sonnet"
) -> dict[str, tuple[str, str]]:
    """한 축의 문서 묶음을 1콜로 판정한다. document_id -> (stance, 근거 인용)."""
    statement = AXIS_STATEMENTS[axis]
    crit = AXIS_CRITERIA[axis]
    numbered = "\n\n".join(f"[{i}] {d['text']}" for i, d in enumerate(docs, 1))
    system = (
        "너는 근거 분류기다. 각 문서가 주어진 가설에 대해 어느 방향의 증거인지 판정한다. "
        "논리적 함의가 아니라 **증거로서의 방향**을 본다. "
        "아래 판정 기준에 해당하면 주저하지 말고 supports 또는 contradicts로 표시하라. "
        "기준 어디에도 해당하지 않을 때만 neutral이다 — neutral은 기본값이 아니라 "
        "'이 축과 무관하다'는 별도의 판정이다.\n"
        "판정마다 그 근거가 된 부분을 문서에서 **그대로 인용**해 함께 낸다. "
        "인용할 대목이 없으면 그 판정은 neutral이어야 한다.\n"
        "설명 없이 JSON object 하나만 반환한다."
    )
    user = (
        f"가설: {statement}\n\n"
        f"supports 기준 : {crit['supports']}\n"
        f"contradicts 기준: {crit['contradicts']}\n"
        f"neutral        : 위 두 기준 어디에도 해당하지 않는다\n\n"
        f"아래 {len(docs)}개 문서 각각을 판정하라.\n"
        '반환 형식: {"1": {"stance": "contradicts", "span": "원문 인용"}, ...}\n'
        "  - 키는 문서 번호 문자열\n"
        "  - stance는 supports / contradicts / neutral 중 하나\n"
        "  - span은 해당 문서에서 그대로 옮긴 200자 이내 인용. neutral이면 빈 문자열\n\n"
        f"{numbered}"
    )
    out = invoke_claude_json(
        system=system, user=user, model_tier=model_tier, temperature=0.0
    )

    result = {}
    for i, doc in enumerate(docs, 1):
        item = out.get(str(i))
        # 구형 포맷(문자열만)도 받아들인다 — 프롬프트 변경 전 실행과 비교할 때 필요하다.
        if isinstance(item, str):
            label, span = item, ""
        elif isinstance(item, dict):
            label, span = item.get("stance", "neutral"), item.get("span", "")
        else:
            label, span = "neutral", ""
        label = str(label).strip().lower()
        result[doc["document_id"]] = (
            label if label in VALID else "neutral",
            str(span or "")[:400],
        )
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
    # 문서 DOC_CHARS자 × BATCH + 지시문 ~700자를 영문 3.6자/토큰으로 환산.
    # 출력은 판정 + 근거 인용이라 문서당 ~60토큰으로 본다.
    est = calls * ((DOC_CHARS * BATCH + 700) / 3.6 / 1e6 * p_in + BATCH * 60 / 1e6 * p_out)
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

    updates: list[tuple[str, str, str, str]] = []   # (stance, span, document_id, axis)
    done = 0
    for axis, docs in by_axis.items():
        for i in range(0, len(docs), BATCH):
            chunk = docs[i : i + BATCH]
            for doc_id, (stance, span) in tag_batch(axis, chunk, args.tier).items():
                updates.append((stance, span, doc_id, axis))
            done += len(chunk)
            snap = usage_snapshot()
            print(f"  {axis:<18} {done}/{len(targets)}  누적 ${snap['cost_usd']:.4f}")

    # 축별 결과를 documents.meta에 넣는다 — 컬럼 추가 없이 스키마를 건드리지 않는다.
    # 판정과 근거를 별도 키로 나눈다(stance_<축> / stance_<축>_span). 한 객체로 묶으면
    # 기존에 문자열을 읽던 쪽이 깨지는데, 지금 그 대가를 치를 이유가 없다.
    with conn.cursor() as cur:
        for stance, span, doc_id, axis in updates:
            key = f"stance_{axis}{args.tag_suffix}"
            cur.execute(
                "UPDATE documents SET meta = COALESCE(meta,'{}'::jsonb) || %s::jsonb "
                "WHERE document_id = %s",
                (json.dumps({key: stance, f"{key}_span": span}), doc_id),
            )
    conn.commit()

    snap = usage_snapshot()
    print(f"\n완료 — {len(updates)}건 갱신, 실측 ${snap['cost_usd']:.4f} "
          f"({snap['calls']}콜 / in {snap['input_tokens']:,} / out {snap['output_tokens']:,})")


if __name__ == "__main__":
    main()
