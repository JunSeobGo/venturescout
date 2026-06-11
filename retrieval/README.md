# Track B — 검색 & 임베딩 (+ ②③ 에이전트)

- 임베딩(모델 결정 PatentSBERTa/KorPatBERT) → documents/claim_limitations.embedding
- pgvector + tsvector 하이브리드 + rerank
- tool: `retrieve()`, `vector_search()` (반환 타입 = shared.contracts, 시그니처 고정)
- 후보를 `ip_overlap_candidates`에 적재 → ⑤가 읽음
- 에이전트: ② Market(full), ③ Competitor(light)

self-eval: Precision@K, contradiction coverage, 검색 latency
