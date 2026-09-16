"""라벨셋 한글화 — 영문 코퍼스를 한국어로 훑어보며 라벨링할 수 있게 한다.

    python -m eval.translate_labels --in eval/labels/retrieval_labels_v2.json \
           --carry eval/labels/retrieval_labels.json --dry-run
    python -m eval.translate_labels --in eval/labels/retrieval_labels_v2.json \
           --carry eval/labels/retrieval_labels.json --yes

코퍼스(리뷰·경쟁사·가격·특허)가 전부 영문이라 발췌를 그대로 보여주면 판단이
느려진다. v1 라벨셋에는 `_한글요약`이 손으로 채워져 있었는데 생성 코드가 없어
후보를 다시 뽑자 사라졌다 — 그 자리를 메운다.

채우는 키(전부 `_` 접두사라 지표 계산에는 쓰이지 않는다):
    _질문_한글   쿼리당 1개. 질의문을 한 문장으로
    _판단기준    쿼리당 1개. 라벨링할 때 스스로에게 던질 질문
    _한글요약    후보당 1개. 발췌를 60자 안팎으로 압축

**번역이 아니라 요약이다.** 전문을 옮기면 읽는 양이 그대로라 빨라지지 않는다.
"무엇에 대한 불만/주장인가"만 남긴다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from agents.llm import _price, invoke_claude_json, reset_usage, usage_snapshot

BATCH = 10
EXCERPT = 400


def _carry_over(path: pathlib.Path | None) -> dict[str, str]:
    """기존 라벨셋의 한글요약을 document_id로 인덱싱한다(쿼리가 달라도 재사용 가능)."""
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for q in data.get("queries", []):
        for doc_id, lab in q.get("labels", {}).items():
            if lab.get("_한글요약"):
                out[doc_id] = lab["_한글요약"]
    print(f"기존 한글요약 {len(out)}건 이어받음 ({path})")
    return out


def summarize_batch(items: list[tuple[str, dict]], model_tier: str) -> dict[str, str]:
    """후보 묶음을 1콜로 요약한다. document_id -> 한글 요약."""
    numbered = "\n\n".join(
        f"[{i}] {(lab.get('_excerpt') or '')[:EXCERPT]}"
        for i, (_, lab) in enumerate(items, 1)
    )
    system = (
        "너는 영문 문서를 한국어로 요약한다. 번역이 아니라 **압축**이다 — "
        "'무엇에 대한 어떤 주장/불만인가'만 60자 안팎으로 남긴다. "
        "제품명·금액·수치는 살리고 수식어는 버린다. "
        "문장은 '~라는 평', '~고 지적', '~을 다룸'처럼 명사형으로 끝낸다. "
        "설명 없이 JSON object 하나만 반환한다."
    )
    user = (
        f"아래 {len(items)}개 문서를 각각 요약하라.\n"
        '반환 형식: {"1": "요약문", "2": "요약문", ...} — 키는 문서 번호 문자열.\n\n'
        f"{numbered}"
    )
    out = invoke_claude_json(
        system=system, user=user, model_tier=model_tier, temperature=0.0
    )
    return {
        doc_id: str(out.get(str(i), "")).strip()
        for i, (doc_id, _) in enumerate(items, 1)
        if str(out.get(str(i), "")).strip()
    }


def translate_queries(queries: list[dict], model_tier: str) -> None:
    """쿼리 25개의 _질문_한글·_판단기준을 한 콜로 채운다(제자리 수정)."""
    todo = [q for q in queries if not q.get("_질문_한글")]
    if not todo:
        return
    numbered = "\n".join(f"[{i}] {q['query']}" for i, q in enumerate(todo, 1))
    out = invoke_claude_json(
        system=(
            "너는 영문 가설 문장을 한국어 한 문장으로 옮긴다. "
            "직역이 아니라 한국어로 자연스럽게, 원래 주장을 그대로 유지한다. "
            "설명 없이 JSON object 하나만 반환한다."
        ),
        user=(
            f"아래 {len(todo)}개 문장을 한국어로 옮겨라.\n"
            '반환 형식: {"1": "한국어 문장", ...}\n\n' + numbered
        ),
        model_tier=model_tier,
        temperature=0.0,
    )
    for i, q in enumerate(todo, 1):
        ko = str(out.get(str(i), "")).strip()
        if ko:
            q["_질문_한글"] = ko
            q["_판단기준"] = f"아래 문서가 '{ko}' 를 판단하는 데 도움이 되는가?"


def main() -> None:
    p = argparse.ArgumentParser(description="라벨셋 한글 요약 채우기")
    p.add_argument("--in", dest="path", type=pathlib.Path, required=True)
    p.add_argument("--carry", type=pathlib.Path, help="기존 라벨셋 — 한글요약을 이어받는다")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--tier", default="haiku", choices=["sonnet", "haiku"])
    p.add_argument("--top", type=int, default=0,
                   help="쿼리별 검색점수 상위 N건만 — 먼저 라벨링할 범위만 채울 때")
    args = p.parse_args()

    data = json.loads(args.path.read_text(encoding="utf-8"))
    carried = _carry_over(args.carry)

    n_carried = 0
    for q in data["queries"]:
        for doc_id, lab in q["labels"].items():
            if not lab.get("_한글요약") and doc_id in carried:
                lab["_한글요약"] = carried[doc_id]
                n_carried += 1
    if n_carried:
        print(f"  → {n_carried}건 재사용")

    # 대상 선별. --top이면 쿼리별 검색점수 상위 N건만.
    pending: list[tuple[str, dict]] = []
    for q in data["queries"]:
        items = sorted(q["labels"].items(),
                       key=lambda kv: kv[1].get("_relevance_score") or 0.0,
                       reverse=True)
        if args.top:
            items = items[: args.top]
        pending += [(d, l) for d, l in items if not l.get("_한글요약")]

    calls = -(-len(pending) // BATCH) + (1 if any(
        not q.get("_질문_한글") for q in data["queries"]) else 0)
    p_in, p_out = _price(args.tier)
    est = calls * ((EXCERPT * BATCH + 500) / 3.6 / 1e6 * p_in + BATCH * 55 / 1e6 * p_out)
    print(f"요약 필요 {len(pending)}건 → {calls}콜  [{args.tier}]  예상 ${est:.2f}")
    if args.dry_run:
        return
    if not pending and all(q.get("_질문_한글") for q in data["queries"]):
        sys.exit("채울 것이 없다.")
    if not args.yes and input("진행할까? [y/N] ").strip().lower() != "y":
        sys.exit("취소")

    reset_usage()
    translate_queries(data["queries"], args.tier)
    args.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    by_id = {d: l for q in data["queries"] for d, l in q["labels"].items()}
    done = 0
    for i in range(0, len(pending), BATCH):
        chunk = pending[i : i + BATCH]
        for doc_id, ko in summarize_batch(chunk, args.tier).items():
            # 같은 문서가 여러 쿼리에 걸쳐 있으면 전부 채운다
            for q in data["queries"]:
                if doc_id in q["labels"]:
                    q["labels"][doc_id]["_한글요약"] = ko
            by_id[doc_id]["_한글요약"] = ko
        done += len(chunk)
        snap = usage_snapshot()
        print(f"  {done}/{len(pending)}  누적 ${snap['cost_usd']:.4f}")
        args.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    snap = usage_snapshot()
    print(f"\n완료 — {done}건 요약, 실측 ${snap['cost_usd']:.4f} ({snap['calls']}콜)")
    print("다음: python -m eval.label_cli --top 5")


if __name__ == "__main__":
    main()
