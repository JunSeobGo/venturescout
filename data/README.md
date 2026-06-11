# Track A — 데이터 수집 & 저장

채우는 테이블: `documents`(특허+시드), `patent_claims`, `claim_limitations`
- 수집(소스별 USPTO/KIPRIS/BigQuery) → 파싱 → 독립항 추출 → limitation 분해 → 적재
- 메타(출처·신뢰도·수집일·stance) 태깅
- 산출물: 채워진 PostgreSQL, raw(S3)
- D6~ 인덱싱 후 C의 ④⑤ 프롬프트·평가 케이스 보조

self-eval: 적재 누락·중복률, 메타 완전성, 수집 재현성
