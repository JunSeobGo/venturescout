"""라벨링 도우미 — JSON을 직접 열지 않고 한 건씩 답만 하면 된다.

    python -m eval.label_cli

키 하나로 답한다. 답할 때마다 즉시 저장되므로 중간에 끊어도 이어서 할 수 있다.

라벨링 기준(annotation guideline):
  질문에 답하는 데 **보탬이 되는 문서인가**만 본다.
  같은 업계·같은 제품군이라는 이유로 '관련 있음'을 주지 않는다.
  애매하면 '아니오'가 안전하다 — 정답을 넉넉히 주면 precision이 부풀려진다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

DEFAULT_PATH = pathlib.Path("eval/labels/retrieval_labels.json")

HELP = """
  y  이 질문에 답하는 데 도움이 된다 (관련 있음)
  n  상관없다 (관련 없음)
  s  건너뛰기 (나중에)
  q  저장하고 종료
"""

STANCE_HELP = """
     엔터  그냥 관련만 있음 (neutral)   ← 대부분 이거
     s     질문이 맞다는 근거 (supports)
     c     질문이 틀렸다는 근거 (contradicts)  ← 보이면 꼭 표시
"""


def _save(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _ask(prompt: str, valid: set[str], default: str | None = None) -> str:
    while True:
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"
        if not raw and default is not None:
            return default
        if raw in valid:
            return raw
        print(f"  ! {'/'.join(sorted(valid))} 중에서 입력해라")


def run(path: pathlib.Path, redo: bool) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))

    todo = [
        (q, doc_id, lab)
        for q in data["queries"]
        for doc_id, lab in q["labels"].items()
        if redo or lab.get("relevant") is None
    ]
    total = sum(len(q["labels"]) for q in data["queries"])
    done = total - len([1 for q in data["queries"]
                        for l in q["labels"].values() if l.get("relevant") is None])

    if not todo:
        print(f"전부 라벨링됐다 ({total}건). 다시 하려면 --redo")
        return

    print(f"\n남은 {len(todo)}건 / 전체 {total}건 (완료 {done}건)")
    print(HELP)

    current_query_id = None
    for i, (query, doc_id, lab) in enumerate(todo, 1):
        if query["query_id"] != current_query_id:
            current_query_id = query["query_id"]
            print("\n" + "=" * 68)
            print(f"[질문] {query.get('_질문_한글') or query['query']}")
            print("=" * 68)

        print(f"\n({i}/{len(todo)})  검색순위점수 {lab.get('_relevance_score')}")
        print(f"  {lab.get('_한글요약') or lab.get('_excerpt', '')[:160]}")

        ans = _ask("  → 이 질문에 도움이 되나? [y/n/s/q] ", {"y", "n", "s", "q"})
        if ans == "q":
            break
        if ans == "s":
            continue

        lab["relevant"] = (ans == "y")

        if ans == "y":
            print(STANCE_HELP)
            st = _ask("  → 입장? [엔터/s/c] ", {"", "s", "c", "n"}, default="")
            lab["stance"] = {"s": "supports", "c": "contradicts"}.get(st, "neutral")

        _save(path, data)      # 매번 저장 — 중간에 끊겨도 안 날아간다

    remaining = len([1 for q in data["queries"]
                     for l in q["labels"].values() if l.get("relevant") is None])
    print(f"\n저장했다. 남은 {remaining}건")
    if remaining == 0:
        print("전부 완료. 이제 지표를 계산할 수 있다:")
        print("  docker compose run --rm -e DATABASE_URL=postgresql://vs:vs_local@db:5432/venturescout \\")
        print("    api python -m eval.labelset")
    else:
        print("이어서 하려면 같은 명령을 다시 실행하면 된다.")


def main() -> None:
    p = argparse.ArgumentParser(description="라벨링 도우미")
    p.add_argument("--path", type=pathlib.Path, default=DEFAULT_PATH)
    p.add_argument("--redo", action="store_true", help="이미 라벨링한 것도 다시")
    args = p.parse_args()
    if not args.path.exists():
        sys.exit(f"라벨셋이 없다: {args.path}")
    run(args.path, args.redo)


if __name__ == "__main__":
    main()
