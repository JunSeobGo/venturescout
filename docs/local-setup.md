# 로컬 셋업 — AWS 없이 검색 계층 재현

부트캠프 종료로 공용 인프라가 정리됐다. 이 문서는 **AWS 없이 로컬에서만**
데이터·검색 계층을 다시 세우는 절차다.

```
AWS 자격증명   ❌ 무효
RDS 인스턴스   ❌ 삭제됨 (호스트 DNS 미해석)
Bedrock        ❌ 사용 불가
시드 270건     ✅ 레포에 커밋돼 있음 (data/{competitors,pricing,reviews}/*.json)
특허 코퍼스    ❌ 미보존 (data/raw는 .gitignore, 원본은 S3/RDS에만 있었음)
```

## 이 셋업으로 되는 것 / 안 되는 것

| | 코퍼스 | 상태 |
|---|---|---|
| ② Market (H1 고객문제) | `seed_review` | ✅ |
| ③ Competitor (H2 경쟁) | `seed_competitor` | ✅ |
| ⑥ BM (H3 수익모델) | `seed_pricing` | ✅ |
| ④ Tech (H4 기술) | 특허 | ❌ 코퍼스 없음 |
| ⑤ IP (H5 특허중첩) | 특허 | ❌ 코퍼스 없음 |
| 전체 그래프 실행 (①~⑧) | — | ❌ Bedrock 필요 |

**즉 검색·평가 계층만 살아난다.** 에이전트 실행에는 LLM이 필요해 별도 자격증명
(Bedrock 또는 다른 provider)이 있어야 한다.

그래도 이 범위로 충분한 것: **`precision@5` / `contradiction_coverage` 측정.**
검색 지표는 임베딩과 DB만 쓰고 LLM을 호출하지 않는다 → 비용 0원.

---

## 절차

### 0. 준비물

- Docker Desktop (실행 중이어야 함)
- 디스크 여유 ~3GB (torch + PatentSBERTa 모델)

### 1. 로컬 DB 기동

```bash
docker compose up -d db
docker compose logs -f db     # "database system is ready to accept connections" 대기
```

> **포트는 호스트 5433**이다(컨테이너 내부는 5432). 로컬에 PostgreSQL이 설치돼 있으면
> 5432를 선점해 컨테이너가 아니라 그쪽으로 붙는다 — 실제로 이 환경에서 발생했다
> (`postgresql-x64-18` 서비스). 컨테이너끼리는 `db:5432`로 통신하므로 영향 없다.

`db/init.sql`이 **볼륨이 비어 있을 때만 1회** 자동 실행된다(9테이블 + pgvector/tsvector 인덱스).
스키마를 다시 깔려면 볼륨부터 지운다:

```bash
docker compose down -v        # ⚠️ pgdata 볼륨 삭제 — 적재한 데이터가 전부 날아간다
```

### 2. 접속 문자열

이후 모든 명령에 아래를 쓴다. `config.db_dsn`은 `DATABASE_URL`을 최우선으로 보므로
`.env`의 죽은 RDS 설정(`POSTGRES_HOST`, `RDS_SECRET_ARN`)을 덮어쓴다.

```bash
# 컨테이너 안에서 실행할 때 (호스트명 db)
DATABASE_URL=postgresql://vs:vs_local@db:5432/venturescout

# 호스트에서 직접 실행할 때 (컨테이너 5432 -> 호스트 5433 매핑)
DATABASE_URL=postgresql://vs:vs_local@localhost:5433/venturescout
```

### 3. 시드 270건 적재

```bash
docker compose run --rm \
  -e DATABASE_URL=postgresql://vs:vs_local@db:5432/venturescout \
  api python -m data.load_seed
```

`data/{competitors,pricing,reviews}/*.json`을 읽어 `documents`에 넣는다.
`(source_type, lower(title))` 중복 체크가 있어 **재실행해도 안전**하다.

기대 출력:

```
seed_competitor       : 90건
seed_pricing          : 90건
seed_review           : 90건
```

### 4. 임베딩 생성

```bash
docker compose run --rm \
  -e DATABASE_URL=postgresql://vs:vs_local@db:5432/venturescout \
  api python -m pipeline.indexer
```

`documents.clean_text` → 768d 벡터(PatentSBERTa). `embedding IS NULL`인 행만 처리하므로
중간에 끊겨도 다시 돌리면 이어간다. 270건이면 CPU로 수 분.

> 모델은 Dockerfile 빌드 시 이미지에 구워져 있고 `hf_cache` 볼륨에 유지된다(ADR-036).
> 첫 실행에서 수백 MB를 받는다면 캐시가 비어 있는 것이니 그대로 기다리면 된다.

### 5. 확인

```bash
docker compose exec db psql -U vs -d venturescout -c \
  "SELECT source_type, count(*), count(embedding) AS embedded
     FROM documents GROUP BY source_type ORDER BY 1;"
```

`count`와 `embedded`가 같으면 성공이다.

---

## 검색 지표 측정

여기부터가 이 셋업의 목적이다. **LLM을 쓰지 않는다.**

```bash
# 라벨링 후보 생성 (5쿼리 × 후보 10)
docker compose run --rm \
  -e DATABASE_URL=postgresql://vs:vs_local@db:5432/venturescout \
  api python -m eval.build_labelset --pool 10

# eval/labels/retrieval_labels.json 을 열어 각 문서의 relevant를 true/false로 채운다
# 기준: eval/labels/README.md

# 지표 계산
docker compose run --rm \
  -e DATABASE_URL=postgresql://vs:vs_local@db:5432/venturescout \
  api python -m eval.labelset
```

`eval/build_labelset.py`의 `QUERIES`에 특허 축(H4/H5) 쿼리는 넣지 않았다 —
코퍼스가 없어 측정이 불가능하기 때문이다. README에 수치를 쓸 때 이 범위를 함께 밝혀라.

---

## 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| 접속 시 `UnicodeDecodeError: 'utf-8' codec can't decode byte ...` | **포트 충돌.** 로컬 설치형 PostgreSQL이 5432를 잡고 있어 그쪽으로 붙었고, 인증 실패 메시지가 한국어 로케일이라 psycopg2가 디코딩하다 터진 것. 호스트 포트는 **5433**을 써라 (`netstat -ano \| findstr :5432`로 점유 확인) |
| `python -m pipeline.indexer`가 출력 없이 exit 0, 임베딩 0건 | 과거 `indexer.py`에 `__main__` 블록이 없어 임포트만 되고 끝났다. 현재는 진입점이 있다 — 그래도 재현되면 모듈을 최신으로 받았는지 확인 |
| `getaddrinfo failed` (호스트 `venturescout-db...`) | 죽은 RDS를 보고 있다. `DATABASE_URL`을 안 넘겼거나 오타 |
| `could not translate host name "db"` | 호스트에서 직접 실행 중인데 컨테이너용 DSN을 썼다. `localhost`로 바꿔라 |
| `relation "documents" does not exist` | `init.sql`이 안 돌았다. 볼륨이 이미 있었을 가능성 → `docker compose down -v` 후 재기동 |
| `extension "vector" is not available` | 이미지가 `pgvector/pgvector:pg16`인지 확인. 순정 `postgres` 이미지로는 안 된다 |
| 임베딩이 매번 처음부터 | `hf_cache` 볼륨이 안 붙었다. `docker compose config`로 확인 |
| 검색 결과가 0건 | 4단계 임베딩을 안 돌렸다. `count(embedding)` 확인 |

## 알려진 제약

- **특허 코퍼스 없음** — 기본 상태에서는 H4/H5 재현 불가. 채우려면 아래 "특허 코퍼스(선택)" 참조
- **LLM 없음** — `agents/graph.py` 전체 실행 불가. `RETRIEVAL=live`는 DB만 요구하므로 검색은 된다
- **`.env`의 AWS 항목은 죽은 값** — 지우지 않고 뒀다. 새 자격증명이 생기면 그대로 쓸 수 있다

## 검증 기록

2026-09-16, 이 절차대로 1~5단계를 실제로 실행해 확인했다.

```
9테이블 + 확장 4종(vector/pgcrypto/pg_trgm/plpgsql) + HNSW·GIN 인덱스   생성 확인
agent_runs_agent_name_check 에 'alternatives' 포함                     확인
시드 적재            270 / 270건
임베딩               270 / 270건, 768차원
HNSW 재생성          완료
```

실행 중 두 가지를 고쳤다:

- **호스트 포트 5432 → 5433** — 로컬 설치형 PostgreSQL과 충돌
- **`pipeline/indexer.py`에 `__main__` 진입점 추가** — 없어서 `python -m`이 임포트만 하고
  조용히 끝났다. 문서 오류가 아니라 원래 있던 버그다

## 특허 코퍼스 (선택)

시드만으로는 H4(tech)·H5(ip)가 근거 0건이라 판정이 항상 `more_research`로 고정된다.
특허를 채우려면 `data/collect_from_hupd.py`를 쓴다 — 원래 경로였던 BigQuery는 GCP
계정이 필요했지만, HUPD(HuggingFace 공개 데이터셋)는 가입 없이 받을 수 있다.

```bash
pip install datasets
python -m data.collect_from_hupd --cpc G06Q30 --limit 800
python -m data.collect_from_hupd --load data/patents/hupd_G06Q30.json
python -m pipeline.indexer        # claim_limitations 임베딩까지
```

HUPD는 2004~2014 **출원**이라 원래 범위(2021~2024 등록특허)와 다르다.
수치를 인용할 때 데이터 범위를 함께 밝힐 것.
