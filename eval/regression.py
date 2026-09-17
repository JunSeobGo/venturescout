"""회귀 실행기 — 한 명령으로 테스트 + 검색 지표를 재고 직전 기록과 비교한다.

    python -m eval.regression                    # 재고 비교 (LLM 비용 0)
    python -m eval.regression --save             # 기준선으로 저장
    python -m eval.regression --provisional      # LLM 초벌을 정답으로 가정해 계산

왜 필요한가. 지금까지 지표를 잴 때마다 스크립트를 새로 써서 돌렸고, 그 결과가
커밋 메시지에만 남았다. 그래서 "NDCG 0.598 → 0.619"가 내 변경 때문인지 라벨
수정 때문인지 구분하는 데 매번 수작업이 들었다(실제로 한 번 틀렸다). 기준선을
파일로 남기고 diff를 찍으면 그 혼동이 사라진다.

**LLM을 쓰지 않는다.** 로컬 임베딩 + DB 검색만 하므로 반복 실행에 비용이 0이다.

`--provisional`은 사람이 확인하지 않은 LLM 초벌(`_llm_relevant`)을 정답 자리에
채워 계산한다. 절대값을 믿으면 안 되고 **설정 간 차이를 보는 용도**다.
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import subprocess
import sys
import time

from config import config
from eval import labelset

BASELINE = pathlib.Path("eval/labels/baseline.json")
METRICS = ("precision_at_k_ceiling", "precision_at_k", "recall_at_k_pooled",
           "mrr", "ndcg_at_k", "contradiction_coverage")


def _provisional(path: pathlib.Path) -> pathlib.Path:
    """LLM 초벌을 정답 자리에 채운 임시 라벨셋을 만든다."""
    data = copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
    for q in data["queries"]:
        for lab in q["labels"].values():
            if lab.get("relevant") is None and "_llm_relevant" in lab:
                lab["relevant"] = lab["_llm_relevant"]
                lab["stance"] = lab.get("_llm_stance", "neutral")
    tmp = path.with_suffix(".provisional.json")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp


def run_tests() -> tuple[bool, str]:
    """테스트를 돌리고 (통과여부, 요약)을 돌려준다.

    종료코드로 판정한다 — 출력만 보고 넘어가면 실패를 놓친다(실제로 한 번 놓쳤다).
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
        capture_output=True, text=True,
    )
    last = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    return proc.returncode == 0, (last[-1] if last else "출력 없음")


def measure(path: pathlib.Path | None, k: int) -> dict:
    t0 = time.perf_counter()
    result = labelset.evaluate_retrieval(path, k)
    return {
        "metrics": {m: result.get(m) for m in METRICS},
        "queries": result.get("queries"),
        "scored_queries": result.get("scored_queries"),
        "unscorable_queries": result.get("unscorable_queries", []),
        "config": {
            "fusion_mode": config.fusion_mode,
            "vector_weight": config.vector_weight,
            "keyword_weight": config.keyword_weight,
            "top_k_fetch": config.top_k_fetch,
            "embedding_model": config.embedding_model,
            "rerank_w": [config.rerank_relevance_w, config.rerank_reliability_w,
                         config.rerank_freshness_w, config.rerank_contradiction_w],
        },
        "k": k,
        "elapsed_sec": round(time.perf_counter() - t0, 1),
    }


def _diff_line(name: str, now, before) -> str:
    if now is None and before is None:
        return f"  {name:24}{'—':>10}{'—':>10}{'':>10}"
    n = "—" if now is None else f"{now:.3f}"
    b = "—" if before is None else f"{before:.3f}"
    if now is None or before is None:
        d = ""
    else:
        delta = now - before
        d = "동일" if abs(delta) < 1e-9 else f"{delta:+.3f}"
    return f"  {name:24}{b:>10}{n:>10}{d:>10}"


def main() -> None:
    p = argparse.ArgumentParser(description="회귀 실행기 (테스트 + 검색 지표)")
    p.add_argument("--path", type=pathlib.Path, help="라벨셋 경로")
    p.add_argument("-k", type=int, default=labelset.DEFAULT_K)
    p.add_argument("--save", action="store_true", help="이번 결과를 기준선으로 저장")
    p.add_argument("--provisional", action="store_true",
                   help="LLM 초벌을 정답으로 가정 (절대값 아님, 설정 비교용)")
    p.add_argument("--skip-tests", action="store_true")
    args = p.parse_args()

    if not args.skip_tests:
        ok, summary = run_tests()
        print(f"테스트  {'통과' if ok else '실패'}  —  {summary}")
        if not ok:
            sys.exit("테스트가 실패했다. 지표를 재기 전에 고쳐라.")

    path = args.path
    tmp = None
    if args.provisional:
        tmp = _provisional(path or labelset.DEFAULT_LABELSET)
        path = tmp
        print("※ 잠정 모드 — LLM 초벌을 정답으로 가정한다. 설정 간 차이만 보라.")

    try:
        now = measure(path, args.k)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()

    cfg = now["config"]
    print(f"\n설정  fusion={cfg['fusion_mode']} "
          f"w={cfg['vector_weight']}/{cfg['keyword_weight']} "
          f"top_k_fetch={cfg['top_k_fetch']}  ({now['elapsed_sec']}s)")
    print(f"쿼리  {now['scored_queries']}/{now['queries']} 채점 "
          f"(정답 미라벨 제외 {len(now['unscorable_queries'])}개)")

    before = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
    print(f"\n  {'지표':24}{'기준선':>10}{'현재':>10}{'변화':>10}")
    for m in METRICS:
        prev = (before or {}).get("metrics", {}).get(m)
        print(_diff_line(m, now["metrics"][m], prev))

    if before and before.get("config") != cfg:
        print("\n설정이 기준선과 다르다 — 변화가 코드 때문인지 설정 때문인지 구분할 것:")
        for key in sorted(set(cfg) | set(before.get("config", {}))):
            a, b = before.get("config", {}).get(key), cfg.get(key)
            if a != b:
                print(f"  {key}: {a} → {b}")

    if args.save:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(now, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기준선 저장: {BASELINE}")


if __name__ == "__main__":
    main()
