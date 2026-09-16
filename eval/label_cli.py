"""라벨링 도우미 — JSON을 직접 열지 않고 키 하나씩만 누르면 된다.

    python -m eval.label_cli --top 5      # 지표를 좌우하는 것부터 (권장 시작점)
    python -m eval.label_cli              # 전부

`eval/prelabel.py`가 깔아둔 LLM 초벌 판정이 있으면 그걸 기본값으로 보여준다.
**엔터만 치면 초벌을 그대로 채택**하므로, 맨손 라벨링보다 훨씬 빠르다.
동의하지 않을 때만 키를 눌러 뒤집으면 된다.

왜 초벌을 정답으로 바로 안 쓰는가: 그러면 모델이 만든 라벨로 모델을 채점하는
자기참조가 된다. 사람이 한 번 훑어야 지표가 의미를 갖는다. 다만 "처음부터
판단하기"가 아니라 "틀린 것만 뒤집기"라 부담이 훨씬 작다.

**--top N을 먼저 쓰는 이유.** precision@5·NDCG@5는 검색 상위 5건만 본다.
후보 616건을 다 채우지 않아도 쿼리당 상위 몇 건만 확정하면 그 지표는 확정된다.
(recall은 전체 라벨이 있어야 정확해지므로 나중에 마저 채우면 된다.)

라벨링 기준(annotation guideline):
  질문에 답하는 데 **보탬이 되는 문서인가**만 본다.
  같은 업계·같은 제품군이라는 이유로 '관련 있음'을 주지 않는다.
  애매하면 '관련 없음'이 안전하다 — 정답을 넉넉히 주면 precision이 부풀려진다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

DEFAULT_PATH = pathlib.Path("eval/labels/retrieval_labels_v2.json")

HELP = """
  엔터  LLM 초벌 그대로 채택   ← 대부분 이거
  y     관련 있음 (입장은 초벌 유지)
  n     관련 없음
  s     관련 있음 + 질문이 맞다는 근거 (supports)
  c     관련 있음 + 질문이 틀렸다는 근거 (contradicts)   ← 보이면 꼭 표시
  u     관련 있음 + 방향 없음 (neutral)
  b     직전 건으로 되돌아가기
  q     저장하고 종료
"""

_STANCE_KO = {"supports": "지지", "contradicts": "반박", "neutral": "중립"}


def _save(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _todo(data: dict, top: int, redo: bool) -> list[tuple[dict, str, dict]]:
    """라벨링 대상. top>0이면 쿼리별 검색점수 상위 top건만 — 지표를 좌우하는 쪽부터."""
    out = []
    for q in data["queries"]:
        items = sorted(
            q["labels"].items(),
            key=lambda kv: kv[1].get("_relevance_score") or 0.0,
            reverse=True,
        )
        if top:
            items = items[:top]
        out += [(q, doc_id, lab) for doc_id, lab in items
                if redo or lab.get("relevant") is None]
    return out


def _apply(lab: dict, key: str) -> None:
    llm_rel = lab.get("_llm_relevant")
    llm_st = lab.get("_llm_stance") or "neutral"
    if key == "":                      # 초벌 채택
        lab["relevant"] = bool(llm_rel)
        lab["stance"] = llm_st if llm_rel else "neutral"
    elif key == "y":
        lab["relevant"] = True
        lab["stance"] = llm_st
    elif key == "n":
        lab["relevant"] = False
        lab["stance"] = "neutral"
    else:
        lab["relevant"] = True
        lab["stance"] = {"s": "supports", "c": "contradicts", "u": "neutral"}[key]


def run(path: pathlib.Path, top: int, redo: bool) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    todo = _todo(data, top, redo)
    total = sum(len(q["labels"]) for q in data["queries"])
    done = sum(1 for q in data["queries"]
               for l in q["labels"].values() if l.get("relevant") is not None)

    if not todo:
        print(f"이 범위는 전부 라벨링됐다 (전체 {total}건 중 {done}건 완료).")
        print("범위를 넓히려면 --top을 키우거나 빼고, 다시 하려면 --redo")
        return

    scope = f"상위 {top}건씩" if top else "전체"
    print(f"\n대상 {len(todo)}건 ({scope}) / 전체 {total}건 (이미 완료 {done}건)")
    print(HELP)

    valid = {"", "y", "n", "s", "c", "u", "b", "q"}
    i, seen_query = 0, None
    while i < len(todo):
        query, _doc_id, lab = todo[i]
        if query["query_id"] != seen_query:
            seen_query = query["query_id"]
            print("\n" + "=" * 70)
            # 코퍼스가 영문이라 한글요약이 있으면 그쪽을 먼저 보여준다
            # (`eval/translate_labels.py`가 채운다). 없으면 원문으로 떨어진다.
            print(f"[질문] {query.get('_질문_한글') or query['query']}")
            if query.get("_질문_한글"):
                print(f"       원문: {query['query']}")
            print(f"       축: {query.get('axis')}")
            print("=" * 70)

        llm_rel = lab.get("_llm_relevant")
        if llm_rel is None:
            hint = "초벌 없음"
        else:
            hint = ("관련있음 / " + _STANCE_KO.get(lab.get("_llm_stance"), "중립")
                    if llm_rel else "관련없음")

        print(f"\n({i + 1}/{len(todo)})  검색점수 {lab.get('_relevance_score')}   "
              f"[{lab.get('_source_type')}]")
        if lab.get("_한글요약"):
            print(f"  {lab['_한글요약']}")
        else:
            print(f"  {(lab.get('_excerpt') or '')[:230]}")
        print(f"  LLM 초벌: {hint}")

        try:
            key = input("  → [엔터=채택] y/n/s/c/u/b/q: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            key = "q"
        if key not in valid:
            print("  ! 엔터 또는 y/n/s/c/u/b/q 중에서 입력해라")
            continue
        if key == "q":
            break
        if key == "b":
            i = max(0, i - 1)
            continue

        _apply(lab, key)
        _save(path, data)              # 매번 저장 — 중간에 끊겨도 안 날아간다
        i += 1

    left = sum(1 for q in data["queries"]
               for l in q["labels"].values() if l.get("relevant") is None)
    print(f"\n저장했다. 전체 미라벨 {left}건 남음")
    if top:
        print(f"상위 {top}건 범위를 끝냈다면 precision@5·NDCG@5는 확정이다. "
              "recall까지 정확히 하려면 --top 없이 마저 채워라.")
    print("지표 계산:")
    print("  docker compose run --rm --no-deps -e POSTGRES_HOST=db -e POSTGRES_PORT=5432 \\")
    print(f"    api python -m eval.labelset --path {path}")


def main() -> None:
    p = argparse.ArgumentParser(description="라벨링 도우미 (LLM 초벌 확인)")
    p.add_argument("--path", type=pathlib.Path, default=DEFAULT_PATH)
    p.add_argument("--top", type=int, default=0,
                   help="쿼리별 검색점수 상위 N건만 — 지표를 좌우하는 쪽부터 (권장: 5)")
    p.add_argument("--redo", action="store_true", help="이미 라벨링한 것도 다시")
    args = p.parse_args()
    if not args.path.exists():
        sys.exit(f"라벨셋이 없다: {args.path}")
    run(args.path, args.top, args.redo)


if __name__ == "__main__":
    main()
