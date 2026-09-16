"""라벨링 후보 생성 — 검색을 돌려 사람이 채울 라벨셋 뼈대를 만든다.

사용:
    python -m eval.build_labelset                    # 기본 쿼리 세트로 생성
    python -m eval.build_labelset --pool 10 --out eval/labels/retrieval_labels.json

동작:
    1. QUERIES의 각 쿼리로 실제 검색을 돌린다(에이전트와 같은 retrieve() 경로)
    2. 상위 pool개 문서를 relevant=null 상태로 덤프
    3. 사람이 각 문서의 relevant(true/false)와 stance를 채운다
    4. eval/labelset.py가 그 파일을 읽어 precision@k를 계산한다

pool을 k(=5)보다 크게 잡는 이유: 상위 5개만 라벨링하면 precision@5가 라벨 그대로
나와 버린다. 넉넉히 뽑아 라벨링해야 순위 변화를 관측할 수 있다.

⚠️ DB 접속이 필요하다(.env의 RDS 또는 로컬 postgres). LLM은 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import pathlib

from retrieval.tools import retrieve

# 라벨링 대상 쿼리. 도메인이 갈리도록 on-domain / 중간 / off-domain을 섞는다
# (판정 캘리브레이션 때 쓴 3개 아이디어와 같은 축 — 분포를 비교할 수 있다).
# 쿼리 문장은 영어다: PatentSBERTa가 영문 전용이라 Structuring도 영어로 생성한다.
QUERIES: list[dict] = [
    {
        "query_id": "saas-h1",
        "axis": "customer_problem",
        "query": "Teams lose time because maintenance knowledge is scattered across tools.",
        "source_types": ["seed_review"],
    },
    {
        "query_id": "saas-h2",
        "axis": "competition",
        "query": "Existing work management tools do not cover field maintenance workflows.",
        "source_types": ["seed_competitor"],
    },
    {
        "query_id": "saas-h3",
        "axis": "business_model",
        "query": "Per-seat monthly subscription is viable for small B2B teams.",
        "source_types": ["seed_pricing", "seed_review", "seed_competitor"],
    },
    {
        "query_id": "fintech-h1",
        "axis": "customer_problem",
        "query": "Merchants struggle to reconcile embedded payment settlements.",
        "source_types": ["seed_review"],
    },
    {
        "query_id": "hr-h2",
        "axis": "competition",
        "query": "Global payroll platforms compete on multi-country compliance coverage.",
        "source_types": ["seed_competitor"],
    },
]


def build(pool: int, out: pathlib.Path) -> dict:
    queries_out = []
    for spec in QUERIES:
        items = retrieve(
            spec["axis"],
            spec["query"],
            k=pool,
            source_types=spec.get("source_types"),
        )
        labels = {
            item.document_id: {
                # ↓ 사람이 채울 칸
                "relevant": None,
                "stance": item.stance,        # 검색이 매긴 값 — 확인 후 수정
                # ↓ 판단 근거로만 쓰는 참고 정보(지표 계산에는 안 씀)
                "_source_type": item.source_type,
                "_excerpt": item.evidence_text[:200],
                "_relevance_score": round(item.relevance_score, 4),
            }
            for item in items
        }
        queries_out.append({**spec, "labels": labels})
        print(f"  {spec['query_id']}: {len(labels)}건 후보")

    data = {
        "version": 1,
        "note": (
            "relevant을 true/false로 채워라(null은 미라벨로 취급되어 precision을 낮춘다). "
            "stance는 검색이 매긴 초기값이니 실제 내용을 보고 supports/contradicts/neutral로 고쳐라. "
            "_로 시작하는 키는 참고용이며 지표 계산에 쓰이지 않는다."
        ),
        "pool": pool,
        "queries": queries_out,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="라벨링 후보 생성")
    parser.add_argument("--pool", type=int, default=10, help="쿼리당 후보 수 (기본 10)")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("eval/labels/retrieval_labels.json"),
        help="출력 경로",
    )
    args = parser.parse_args()

    if args.out.exists():
        # 이미 라벨링한 파일을 덮어쓰면 수작업이 날아간다.
        raise SystemExit(
            f"{args.out} 가 이미 있다. 덮어쓰려면 먼저 옮기거나 --out으로 다른 경로를 줘라."
        )

    print(f"검색 실행 중 (쿼리 {len(QUERIES)}개 × 후보 {args.pool}개)...")
    build(args.pool, args.out)
    print(f"\n생성 완료: {args.out}")
    print("이제 각 문서의 relevant를 true/false로 채우고 stance를 확인해라.")
    print("완료 후: python -m eval.labelset")


if __name__ == "__main__":
    main()
