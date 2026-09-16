"""라벨링 후보 생성 — 검색을 돌려 사람이 채울 라벨셋 뼈대를 만든다.

사용:
    python -m eval.build_labelset --out eval/labels/retrieval_labels_v2.json \
           --merge eval/labels/retrieval_labels.json

동작:
    1. QUERIES의 각 쿼리를 **여러 검색 설정으로** 돌려 후보를 모은다(pooling)
    2. 합집합을 relevant=null 상태로 덤프
    3. 사람이 각 문서의 relevant(true/false)와 stance를 채운다
       — `eval/prelabel.py`로 LLM 초벌 판정을 받아두면 확인만 하면 된다
    4. eval/labelset.py가 그 파일을 읽어 지표를 계산한다

**왜 pooling인가.** 초판은 `retrieve()` 한 번의 상위 N건만 후보로 썼다. 그러면
하이브리드 검색이 놓친 문서는 영원히 후보에 들어오지 못하고, 그 문서는 "정답이
아닌 것"으로 취급된다. recall이 구조적으로 과대평가되고, 검색 설정을 바꿔도
후보 밖의 개선은 관측되지 않는다. TREC식 pooling을 따라 **벡터 전용 / 키워드 전용 /
하이브리드** 세 설정의 상위 depth를 합집합으로 모은다. 한 설정이 놓친 문서를
다른 설정이 건져 올린다.

**pool을 k(=5)보다 크게 잡는 이유**: 상위 5개만 라벨링하면 precision@5가 라벨
그대로 나와 버린다. 넉넉히 뽑아야 순위 변화를 관측할 수 있다. 다만 정답 수가
k보다 적으면 precision@k의 천장이 1.0에 못 미친다는 점도 같이 기억할 것
— 쿼리당 정답이 1~2건이면 아무리 잘 검색해도 P@5는 0.2~0.4가 최대다.

⚠️ DB 접속이 필요하다(.env). LLM은 쓰지 않는다 — 초벌 판정은 eval/prelabel.py.
"""
from __future__ import annotations

import argparse
import json
import pathlib

from config import config
from retrieval.tools import retrieve

# 라벨링 대상 쿼리. 코퍼스가 실제로 답할 수 있는 것만 넣는다 —
# 경쟁사 카테고리(recommendation_engine·hcm_erp·work_management·marketing_automation·
# payment_processing·search_discovery)와 특허 CPC(G06Q30 전자상거래)를 보고 골랐다.
# 5개 축 × 5개씩. 쿼리가 적으면 쿼리 하나가 지표의 큰 몫을 차지해 노이즈가 커진다.
# 쿼리 문장은 영어다: PatentSBERTa가 영문 전용이라 Structuring도 영어로 생성한다.
_REVIEW = ["seed_review"]
_COMPET = ["seed_competitor"]
_BIZ = ["seed_pricing", "seed_review", "seed_competitor"]
_PATENT = ["patent"]

QUERIES: list[dict] = [
    # ── customer_problem ─────────────────────────────────────────────────────
    {"query_id": "saas-h1", "axis": "customer_problem", "source_types": _REVIEW,
     "query": "Teams lose time because maintenance knowledge is scattered across tools."},
    {"query_id": "fintech-h1", "axis": "customer_problem", "source_types": _REVIEW,
     "query": "Merchants struggle to reconcile embedded payment settlements."},
    {"query_id": "rec-h1", "axis": "customer_problem", "source_types": _REVIEW,
     "query": "Merchants cannot configure product recommendation rules without engineering help."},
    {"query_id": "hr-h1", "axis": "customer_problem", "source_types": _REVIEW,
     "query": "HR teams lose time reconciling payroll and benefits across multiple countries."},
    {"query_id": "mkt-h1", "axis": "customer_problem", "source_types": _REVIEW,
     "query": "Marketers cannot build audience segments without lengthy setup and vendor training."},

    # ── competition ──────────────────────────────────────────────────────────
    {"query_id": "saas-h2", "axis": "competition", "source_types": _COMPET,
     "query": "Existing work management tools do not cover field maintenance workflows."},
    {"query_id": "hr-h2", "axis": "competition", "source_types": _COMPET,
     "query": "Global payroll platforms compete on multi-country compliance coverage."},
    {"query_id": "rec-h2", "axis": "competition", "source_types": _COMPET,
     "query": "Recommendation engines require deep catalog integration that small merchants cannot afford."},
    {"query_id": "pay-h2", "axis": "competition", "source_types": _COMPET,
     "query": "Payment processors differentiate on cross-border settlement speed and FX cost."},
    {"query_id": "srch-h2", "axis": "competition", "source_types": _COMPET,
     "query": "Site search vendors compete on relevance tuning and merchandising control."},

    # ── business_model ───────────────────────────────────────────────────────
    {"query_id": "saas-h3", "axis": "business_model", "source_types": _BIZ,
     "query": "Per-seat monthly subscription is viable for small B2B teams."},
    {"query_id": "bm-usage", "axis": "business_model", "source_types": _BIZ,
     "query": "Usage-based pricing lowers adoption friction compared with annual contracts."},
    {"query_id": "bm-tier", "axis": "business_model", "source_types": _BIZ,
     "query": "Gating core features behind enterprise tiers pushes mid-market buyers away."},
    {"query_id": "bm-impl", "axis": "business_model", "source_types": _BIZ,
     "query": "Implementation and onboarding fees add significant cost beyond the list price."},
    {"query_id": "bm-free", "axis": "business_model", "source_types": _BIZ,
     "query": "A free tier is necessary to acquire small merchants in this category."},

    # ── technology (특허) ────────────────────────────────────────────────────
    {"query_id": "patent-h4", "axis": "technology", "source_types": _PATENT,
     "query": "A system that retrieves maintenance records and recommends repair procedures."},
    {"query_id": "tech-rec", "axis": "technology", "source_types": _PATENT,
     "query": "Generating personalized product recommendations from browsing and purchase history."},
    {"query_id": "tech-search", "axis": "technology", "source_types": _PATENT,
     "query": "Ranking search results using a machine-learned relevance model over user signals."},
    {"query_id": "tech-seg", "axis": "technology", "source_types": _PATENT,
     "query": "Segmenting customers into groups using behavioral attributes for targeted offers."},
    {"query_id": "tech-price", "axis": "technology", "source_types": _PATENT,
     "query": "Adjusting displayed prices in response to demand signals and inventory levels."},

    # ── ip (특허) ────────────────────────────────────────────────────────────
    {"query_id": "patent-h5", "axis": "ip", "source_types": _PATENT,
     "query": "Recommending items to a user based on transaction history and similarity scoring."},
    {"query_id": "ip-cart", "axis": "ip", "source_types": _PATENT,
     "query": "Detecting abandoned shopping carts and triggering follow-up messages to the shopper."},
    {"query_id": "ip-loyal", "axis": "ip", "source_types": _PATENT,
     "query": "Computing loyalty rewards from purchase frequency and basket value."},
    {"query_id": "ip-ad", "axis": "ip", "source_types": _PATENT,
     "query": "Selecting advertisements to display using auction bids and predicted click-through."},
    {"query_id": "ip-review", "axis": "ip", "source_types": _PATENT,
     "query": "Aggregating user reviews and ratings to influence product ranking in a storefront."},
]

# 후보 pooling에 쓸 검색 설정. (이름, vector_weight, keyword_weight)
# 하이브리드 기본값(0.6/0.4)만으로는 순수 키워드 매칭으로만 잡히는 문서를 놓친다.
POOL_CONFIGS = [
    ("hybrid", None, None),      # config 기본값 그대로
    ("vector", 1.0, 0.0),
    ("keyword", 0.0, 1.0),
]


def _pooled_candidates(spec: dict, depth: int) -> dict:
    """여러 검색 설정의 상위 depth를 합집합으로 모은다. document_id -> EvidenceItem."""
    base_v, base_k = config.vector_weight, config.keyword_weight
    pooled: dict = {}
    per_config: dict[str, int] = {}
    try:
        for name, vw, kw in POOL_CONFIGS:
            config.vector_weight = base_v if vw is None else vw
            config.keyword_weight = base_k if kw is None else kw
            items = retrieve(
                spec["axis"], spec["query"], k=depth,
                source_types=spec.get("source_types"),
            )
            new = 0
            for item in items:
                if item.document_id not in pooled:
                    pooled[item.document_id] = item
                    new += 1
            per_config[name] = new
    finally:
        config.vector_weight, config.keyword_weight = base_v, base_k

    added = " ".join(f"{n}+{c}" for n, c in per_config.items())
    print(f"  {spec['query_id']:14} {len(pooled):3}건   ({added})")
    return pooled


def _carry_over(old: pathlib.Path | None) -> dict:
    """기존 라벨셋의 사람 판단을 (query_id, document_id)로 인덱싱해 둔다.

    후보를 다시 뽑으면 문서 집합이 달라지는데, 이미 사람이 채운 판단까지 날리면
    안 된다. relevant/stance만 옮기고 참고용 `_` 키는 새 값으로 덮는다.
    """
    if not old or not old.exists():
        return {}
    data = json.loads(old.read_text(encoding="utf-8"))
    out = {}
    for q in data.get("queries", []):
        for doc_id, lab in q.get("labels", {}).items():
            if lab.get("relevant") is not None:
                out[(q["query_id"], doc_id)] = {
                    "relevant": lab.get("relevant"),
                    "stance": lab.get("stance"),
                }
    print(f"기존 라벨 {len(out)}건 이어받음 ({old})")
    return out


def build(depth: int, out: pathlib.Path, merge: pathlib.Path | None = None) -> dict:
    carried = _carry_over(merge)
    queries_out, n_carried = [], 0

    for spec in QUERIES:
        pooled = _pooled_candidates(spec, depth)
        labels = {}
        for doc_id, item in pooled.items():
            prior = carried.get((spec["query_id"], doc_id))
            if prior:
                n_carried += 1
            labels[doc_id] = {
                # ↓ 사람이 채울 칸 (기존 판단이 있으면 이어받는다)
                "relevant": prior["relevant"] if prior else None,
                "stance": prior["stance"] if prior else item.stance,
                # ↓ 판단 근거로만 쓰는 참고 정보(지표 계산에는 안 씀)
                "_source_type": item.source_type,
                "_excerpt": item.evidence_text[:240],
                "_relevance_score": round(item.relevance_score, 4),
            }
        queries_out.append({**spec, "labels": labels})

    total = sum(len(q["labels"]) for q in queries_out)
    data = {
        "version": 2,
        "note": (
            "relevant을 true/false로 채워라(null은 미라벨로 취급된다). "
            "stance는 초기값이니 실제 내용을 보고 supports/contradicts/neutral로 고쳐라. "
            "_로 시작하는 키는 참고용이며 지표 계산에 쓰이지 않는다. "
            "후보는 벡터/키워드/하이브리드 세 설정의 합집합(pooling)이다."
        ),
        "pool_depth": depth,
        "pool_configs": [name for name, _, _ in POOL_CONFIGS],
        "queries": queries_out,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n쿼리 {len(queries_out)}개 / 후보 {total}건 "
          f"(쿼리당 평균 {total / len(queries_out):.1f}) / 기존 라벨 {n_carried}건 재사용")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="라벨링 후보 생성 (pooling)")
    parser.add_argument("--depth", type=int, default=15,
                        help="설정당 상위 몇 건을 모을지 (기본 15, 합집합은 이보다 크다)")
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path("eval/labels/retrieval_labels_v2.json"))
    parser.add_argument("--merge", type=pathlib.Path,
                        help="기존 라벨셋 — 사람이 채운 relevant/stance를 이어받는다")
    parser.add_argument("--force", action="store_true", help="출력 파일 덮어쓰기 허용")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        raise SystemExit(
            f"{args.out} 가 이미 있다. --force를 주거나 --out으로 다른 경로를 줘라."
        )

    print(f"검색 실행 중 (쿼리 {len(QUERIES)}개 × 설정 {len(POOL_CONFIGS)}개 × 깊이 {args.depth})...")
    build(args.depth, args.out, args.merge)
    print(f"생성 완료: {args.out}")
    print(f"다음: python -m eval.prelabel --in {args.out}   (LLM 초벌 판정)")


if __name__ == "__main__":
    main()
