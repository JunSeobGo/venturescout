"""LLM 초벌 라벨링 — 사람이 처음부터 판단하지 않고 **확인만** 하면 되게 한다.

    python -m eval.prelabel --in eval/labels/retrieval_labels_v2.json --dry-run
    python -m eval.prelabel --in eval/labels/retrieval_labels_v2.json --yes

왜 필요한가. 라벨링은 이 프로젝트에서 가장 오래 막혀 있던 작업이다(ADR-042).
후보를 쿼리당 10 → 30건으로 늘리면 사람이 볼 양이 3배가 되므로, 맨손으로는
더 못 한다. ADR-047에서 stance 배치 판정이 F1 0.912를 냈으니 같은 방식을
relevant 판단에도 쓴다 — 모델이 먼저 찍고 사람은 뒤집을 것만 뒤집는다.

**초벌은 정답이 아니다.** `_llm_relevant`/`_llm_stance`라는 별도 키에 쓰고,
지표가 읽는 `relevant`/`stance`는 사람이 확인해야 채워진다. 모델 판정을 그대로
정답으로 승격하면 "모델을 모델로 채점"하는 자기참조가 되어 지표가 무의미해진다.
승격은 `eval/label_cli.py`에서 사람이 y/n을 누를 때만 일어난다.

사람이 이미 채운 칸은 건드리지 않는다 — 수작업을 덮어쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from agents.llm import _price, invoke_claude_json, reset_usage, usage_snapshot

BATCH = 10          # 10건을 1콜로 — ADR-046에서 문서당 처리가 느려 기각된 교훈
EXCERPT = 400       # 후보 발췌 길이. 판단에 충분하고 토큰은 아낀다
VALID_STANCE = {"supports", "contradicts", "neutral"}

# 축별 판정 기준. pipeline/stance_llm.py의 AXIS_CRITERIA와 같은 취지다 —
# "확신 없으면 중립"류의 지시가 판정을 한쪽으로 쏠리게 만든다는 걸 확인했으므로
# (ADR-047) 무엇이 관련 있음/반박인지를 명시한다.
AXIS_HINT = {
    "customer_problem": "고객이 겪는 불편·시간/비용 손실에 대한 언급이면 관련 있다.",
    "competition": "경쟁 제품의 역량·포지셔닝·차별점에 대한 언급이면 관련 있다.",
    "business_model": "가격 수준·과금 구조·계약 조건·티어 구성에 대한 언급이면 관련 있다.",
    "technology": "해당 기술 역량의 구현 방식·구성요소에 대한 언급이면 관련 있다.",
    "ip": "청구항이 해당 기법을 다루면 관련 있다. 침해 위험 판단의 근거가 되는가로 본다.",
}


def _pending(data: dict) -> list[tuple[dict, str, dict]]:
    """아직 초벌 판정이 없고 사람도 안 채운 후보만 고른다."""
    out = []
    for q in data["queries"]:
        for doc_id, lab in q["labels"].items():
            if lab.get("relevant") is None and "_llm_relevant" not in lab:
                out.append((q, doc_id, lab))
    return out


def judge_batch(query: dict, items: list[tuple[str, dict]], model_tier: str) -> dict:
    """한 쿼리의 후보 묶음을 1콜로 판정한다. document_id -> {relevant, stance}."""
    axis = query.get("axis", "")
    numbered = "\n\n".join(
        f"[{i}] {(lab.get('_excerpt') or '')[:EXCERPT]}"
        for i, (_, lab) in enumerate(items, 1)
    )
    system = (
        "너는 검색 평가용 라벨러다. 각 문서가 주어진 질의에 **근거로 쓸 만한지**를 "
        "판정한다. 주제가 겹치는 정도가 아니라, 그 질의를 검증하려는 사람이 이 문서를 "
        "읽고 판단에 보탬이 되는지를 본다. "
        "관련 있으면 그 문서가 질의를 지지하는지/반박하는지/방향이 없는지도 함께 낸다. "
        "설명 없이 JSON object 하나만 반환한다."
    )
    user = (
        f"질의: {query['query']}\n"
        f"축: {axis} — {AXIS_HINT.get(axis, '')}\n\n"
        f"아래 {len(items)}개 문서를 각각 판정하라.\n"
        '반환 형식: {"1": {"relevant": true, "stance": "supports"}, ...}\n'
        "  - relevant는 true/false\n"
        "  - stance는 supports / contradicts / neutral (relevant가 false면 neutral)\n\n"
        f"{numbered}"
    )
    out = invoke_claude_json(
        system=system, user=user, model_tier=model_tier, temperature=0.0
    )

    result = {}
    for i, (doc_id, _) in enumerate(items, 1):
        v = out.get(str(i))
        if not isinstance(v, dict):
            continue
        stance = str(v.get("stance", "neutral")).strip().lower()
        result[doc_id] = {
            "relevant": bool(v.get("relevant")),
            "stance": stance if stance in VALID_STANCE else "neutral",
        }
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="LLM 초벌 라벨링 (사람 확인용 힌트)")
    p.add_argument("--in", dest="path", type=pathlib.Path, required=True)
    p.add_argument("--dry-run", action="store_true", help="비용만 추정하고 끝낸다")
    p.add_argument("--yes", action="store_true", help="확인 없이 실행")
    p.add_argument("--tier", default="haiku", choices=["sonnet", "haiku"],
                   help="ADR-047에서 stance 판정 F1 0.912로 검증된 haiku가 기본값")
    p.add_argument("--limit", type=int, default=0, help="처리할 후보 수 제한(시험용)")
    args = p.parse_args()

    data = json.loads(args.path.read_text(encoding="utf-8"))
    pending = _pending(data)
    if args.limit:
        pending = pending[: args.limit]
    if not pending:
        sys.exit("초벌 판정이 필요한 후보가 없다.")

    # 쿼리별로 묶어야 질의문을 한 번만 싣는다
    by_query: dict[str, list] = {}
    for q, doc_id, lab in pending:
        by_query.setdefault(q["query_id"], []).append((q, doc_id, lab))

    calls = sum(-(-len(v) // BATCH) for v in by_query.values())
    p_in, p_out = _price(args.tier)
    est = calls * ((EXCERPT * BATCH + 600) / 3.6 / 1e6 * p_in + BATCH * 30 / 1e6 * p_out)
    print(f"초벌 대상 {len(pending)}건 / 쿼리 {len(by_query)}개 → {calls}콜  "
          f"[{args.tier}]  예상 ${est:.2f}")
    if args.dry_run:
        return
    if not args.yes and input("진행할까? [y/N] ").strip().lower() != "y":
        sys.exit("취소")

    reset_usage()
    done = 0
    for qid, entries in by_query.items():
        query = entries[0][0]
        for i in range(0, len(entries), BATCH):
            chunk = entries[i : i + BATCH]
            verdicts = judge_batch(query, [(d, l) for _, d, l in chunk], args.tier)
            for _, doc_id, lab in chunk:
                v = verdicts.get(doc_id)
                if v:
                    lab["_llm_relevant"] = v["relevant"]
                    lab["_llm_stance"] = v["stance"]
            done += len(chunk)
            snap = usage_snapshot()
            print(f"  {qid:14} {done}/{len(pending)}  누적 ${snap['cost_usd']:.4f}")
            # 콜마다 저장 — 중간에 끊겨도 앞부분이 날아가지 않는다
            args.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    snap = usage_snapshot()
    n_rel = sum(1 for q in data["queries"] for l in q["labels"].values()
                if l.get("_llm_relevant") is True)
    print(f"\n완료 — 초벌 {done}건 (관련있음 추정 {n_rel}건), 실측 ${snap['cost_usd']:.4f} "
          f"({snap['calls']}콜)")
    print("다음: python -m eval.label_cli   — 초벌을 보고 확인/수정만 하면 된다")


if __name__ == "__main__":
    main()
