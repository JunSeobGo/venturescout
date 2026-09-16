"""HUPD(Harvard USPTO Patent Dataset)에서 특허를 받아 로컬 JSON으로 떨군다.

원래 수집 경로는 `collect_from_bigquery.py`(GCP BigQuery) → S3 → `load_from_s3.py`였다.
부트캠프 인프라가 정리되면서 GCP 계정도 S3 버킷도 없어져, **가입 없이 받을 수 있는
HuggingFace 공개 데이터셋**으로 출처만 갈아끼운다.

바뀌는 것은 "특허를 어디서 가져오는가"뿐이다. 청구항 분해(parse_claims /
parse_limitations)와 3테이블 적재(save_to_db)는 `load_from_s3.py`를 그대로 재사용하므로
documents / patent_claims / claim_limitations를 채우는 방식은 종전과 동일하다.

사용:
    # 1) 받아서 JSON으로 저장 (DB 불필요)
    python -m data.collect_from_hupd --cpc G06Q30 --limit 800

    # 2) 저장한 JSON을 DB에 적재
    python -m data.collect_from_hupd --load data/patents/hupd_G06Q30.json

    # 3) 받자마자 바로 적재
    python -m data.collect_from_hupd --cpc G06Q30 --limit 800 --load-now

주의:
- `datasets` 라이브러리가 필요하다: pip install datasets
- HUPD 전체는 125GB다. 날짜 구간을 좁혀 필요한 만큼만 받는다(기본 2개월).
- HUPD는 2004~2014 **출원**이다. 원래 프로젝트는 2021~2024 등록특허였으므로
  범위가 다르다 — 결과를 인용할 때 반드시 데이터 범위를 함께 밝힐 것.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# 청구항 분해·적재는 기존 로직 재사용 (출처만 다르고 처리는 같다)
from data.load_from_s3 import save_to_db

# HUPD 레코드 → save_to_db()가 기대하는 row 형식으로의 필드 대응
FIELD_MAP = {
    "publication_number": "publication_number",
    "title": "title",
    "abstract": "abstract",
    "claims": "claim_text",          # ← parse_claims()의 입력
    "filing_date": "filing_date",
    "patent_issue_date": "grant_date",
    "main_cpc_label": "cpc_code",
}

DEFAULT_OUT = pathlib.Path("data/patents")


def _require_datasets():
    try:
        from datasets import load_dataset  # noqa: F401
    except ImportError:
        sys.exit(
            "datasets 라이브러리가 없다.\n"
            "  pip install datasets\n"
            "설치가 부담되면 --load 로 이미 받아둔 JSON만 적재할 수 있다."
        )


def to_row(record: dict) -> dict | None:
    """HUPD 레코드를 save_to_db()가 먹는 형태로 변환. 청구항이 없으면 버린다."""
    row = {dst: record.get(src) for src, dst in FIELD_MAP.items()}
    # 청구항 텍스트가 없으면 patent_claims/claim_limitations를 만들 수 없다.
    if not (row.get("claim_text") or "").strip():
        return None
    if not row.get("publication_number"):
        return None
    row["assignee"] = None          # HUPD에는 assignee가 없다 → 적재 시 '(출원인 미상)'
    return row


def fetch(cpc_prefix: str, start: str, end: str, limit: int) -> list[dict]:
    """HUPD에서 날짜 구간을 받아 CPC 접두사로 거른다."""
    _require_datasets()
    from datasets import load_dataset

    print(f"HUPD 로드 중 — 출원일 {start} ~ {end}")
    print("  (최초 실행은 메타데이터 내려받느라 오래 걸린다)")
    ds = load_dataset(
        "HUPD/hupd",
        name="sample",
        trust_remote_code=True,      # HUPD는 커스텀 로딩 스크립트를 쓴다
        train_filing_start_date=start,
        train_filing_end_date=end,
    )["train"]
    print(f"  구간 내 {len(ds)}건")

    rows, skipped_cpc, skipped_empty = [], 0, 0
    for record in ds:
        cpc = record.get("main_cpc_label") or ""
        if not cpc.startswith(cpc_prefix):
            skipped_cpc += 1
            continue
        row = to_row(record)
        if row is None:
            skipped_empty += 1
            continue
        rows.append(row)
        if len(rows) >= limit:
            break

    print(f"  CPC {cpc_prefix} 매칭 {len(rows)}건 "
          f"(CPC 불일치 {skipped_cpc} / 청구항 없음 {skipped_empty})")
    if not rows:
        print("  ⚠️  0건이다. --cpc 를 넓히거나(G06Q) 날짜 구간을 늘려라.")
    return rows


def dump(rows: list[dict], out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out}  ({len(rows)}건)")


def load(path: pathlib.Path) -> None:
    rows = json.loads(path.read_text(encoding="utf-8"))
    print(f"{path} 에서 {len(rows)}건 로드 → DB 적재")
    save_to_db(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="HUPD 특허 수집 (BigQuery 대체)")
    p.add_argument("--cpc", default="G06Q30",
                   help="CPC 접두사. 기본 G06Q30(전자상거래). 넓히려면 G06Q")
    p.add_argument("--start", default="2014-01-01", help="출원일 시작")
    p.add_argument("--end", default="2014-02-28", help="출원일 끝")
    p.add_argument("--limit", type=int, default=800, help="최대 수집 건수")
    p.add_argument("--out", type=pathlib.Path, default=None, help="JSON 출력 경로")
    p.add_argument("--load", type=pathlib.Path, default=None,
                   help="수집 없이 기존 JSON만 DB에 적재")
    p.add_argument("--load-now", action="store_true",
                   help="수집 직후 바로 DB 적재")
    args = p.parse_args()

    if args.load:
        load(args.load)
        return

    rows = fetch(args.cpc, args.start, args.end, args.limit)
    if not rows:
        return

    out = args.out or DEFAULT_OUT / f"hupd_{args.cpc}.json"
    dump(rows, out)     # DB 적재가 실패해도 내려받은 건 남기려고 항상 먼저 저장한다

    if args.load_now:
        save_to_db(rows)
    else:
        print(f"\nDB 적재: python -m data.collect_from_hupd --load {out}")


if __name__ == "__main__":
    main()
