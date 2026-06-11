# VentureScout v3 — Evidence 기반 창업 실사 멀티 에이전트 (통합 실행 계획서)

창업 아이디어를 가설로 분해하고, **상충하는 근거를 한 화면에 드러낸 뒤**, Critic이 낙관 편향을 제거해 Go/Pivot/Kill/More Research 신호와 다음 검증 실험을 제안한다.

* **포지셔닝** — verdict를 내리는 오라클이 아니라, 찬반 근거를 추적 가능하게 보여주는 **decision-support 도구**. 신호는 Evidence Board의 부산물.
* **시그니처** — 특허 청구항(claim) 중첩 신호 분석. 도메인 내 유일한 *깨끗한 공식 데이터* 위에서 동작.
* **스코프(확정)** — **혼합**: 5가설 보드는 넓게 띄우되, 깊이 투자는 시그니처(⑤ IP)에 집중. ②는 풀로 폭 증명, ③④⑥은 근거에 묶인 경량 신호.
* **통합 이력** — VentureScout를 뼈대로, 이전 v2의 세 결정(임베딩 모델·데이터 소스 분기·Chainlit 스트리밍)을 세부로 주입. v2의 Chroma·"IP 단독 본체" 포지셔닝은 폐기.

---

## 0. 데이터 소스 분기 — 최우선 Day 1 결정

시그니처(⑤ 청구항 중첩)의 특허 데이터·임베딩이 여기서 갈림. **Day 1 오전 확정.**

| 관점 | **영어(USPTO 벌크 / BigQuery)** | **한국어(KIPRIS)** |
|---|---|---|
| 데이터 확보 | USPTO 공식 download·BigQuery SQL → **쉬움** | 월 1,000회 한도 → 빡셈 |
| 도메인 적합성 | 글로벌 특허, 한국 FTO 못 봄 | **한국 스타트업 IP/FTO 본질 적합** |
| 임베딩 1순위 | **PatentSBERTa**(영어 특허 특화, sentence-transformers) | **KorPatBERT large**(한국어+특허) |
| 임베딩 폴백 | e5-large | PatentSBERTa_V2(다국어) → e5 → PatentSBERTa(번역 전제) |
| 분류 코드 | CPC(세분화 유리) | IPC |
| 승인 리스크 | 없음(즉시 사용) | KorPatBERT 사용신청·승인 대기 |

**결정 가이드**
* 데모 무기가 "한국 스타트업 IP 검증/FTO" → **KIPRIS**
* 10일 내 데이터 확보 안정성·승인 리스크 회피 → **USPTO/BigQuery**
* **Day 1 당일 검증** — USPTO/BigQuery는 dry run, KIPRIS는 수집 PoC(샘플 100건)
* **사전 조치** — KIPRIS 가능성 있으면 KorPatBERT 사용신청·협약서 **즉시 제출**

> 소스를 정하면 임베딩·언어·분류코드가 자동 확정. 나머지(§1~§18)는 소스 무관 공통.

---

## 1. 실행 구조

```text
┌──────────────────────────────────────────────┐
│            사용자 (창업 아이디어 입력)            │
└────────────────────────┬─────────────────────┘
                ┌─────────▼─────────┐
                │   Chainlit UI      │ 입력·스트리밍 진행·Evidence Board
                └─────────┬─────────┘
                          │ REST + SSE(스트리밍)
                ┌─────────▼─────────┐
                │  FastAPI (job)     │ job 생성·상태·스트리밍 이벤트
                └─────────┬─────────┘
        ┌─────────────────▼──────────────────┐
        │   LangGraph 오케스트레이터 (워커)     │
        │  ① 구조화 → ②③④⑤⑥ 분석 → ⑦ Critic   │
        └────┬──────────────────────────┬────┘
        검색 tool                    LLM 호출
   ┌────────▼─────────┐         ┌──────▼──────────┐
   │   PostgreSQL      │         │ Bedrock Converse │
   │  + pgvector       │         └──────────────────┘
   │  + tsvector       │
   │ (운영+근거+검색)    │
   └────────▲─────────┘
            │ 적재
   ┌────────┴────────────────────────────┐
   │  수집기(Python) + 스케줄러(cron)        │
   │  특허 벌크 / 시드 코퍼스 / (선택) 검색    │
   │  └ 원본 파일은 S3 단순 버킷               │
   └─────────────────────────────────────┘
```

**한 건 처리 순서**: 입력 → FastAPI job 생성 → ① 가설 분해(Hypothesis Ledger) → 가설별 검색(찬반 근거 회수) → ②③④⑤⑥ evidence_id 그라운딩 분석 → ⑦ Critic 반박 + Go/Pivot/Kill/More Research + 다음 실험 → Evidence Board 조회.

> 핵심: 무거운 웨어하우스 없이 **PostgreSQL 하나가 운영 DB·근거 저장소·검색 엔진**을 겸한다. 컴포넌트 최소화로 4인이 실제 완주.

---

## 2. 핵심 설계 결정 (Why)

| 결정 | 이유 |
|---|---|
| 메달리온/Glue/Athena 미사용, **Postgres 단일 스토어** | 웨어하우스 요구 규모 아님. pgvector+tsvector로 운영·근거·검색 일원화 → **v2의 Chroma↔PostgreSQL 동기화 리스크 소거** |
| 특허는 관련 CPC/IPC만 선별 수집 | 전수 적재 불필요, 시그니처에 필요한 만큼만 |
| 적대적 스크래핑 소스 제거 → **시드 코퍼스** | G2·Reddit·실시간 pricing은 ToS·anti-bot로 기간 증발. "준비된 데이터셋"으로 정직 표기 |
| **Critic을 멀티에이전트 핵심으로** | 분석축은 정직히 "프롬프트 분해". 단일 호출과 *측정 가능하게 다른* 출력은 Critic 적대검증에서만 |
| 평가는 **process 기반** | 창업 검증은 outcome 정답 없음 → groundedness·overclaim 등 과정 지표 |
| **혼합 스코프** | 5가설 보드로 폭, ⑤ IP 풀 구현으로 깊이. 경량 칸도 근거에 묶어 "죽은 칸" 없게 |
| **임베딩 모델 명시**(PatentSBERTa/KorPatBERT) | 시그니처가 청구항 중첩인데 범용 임베딩이면 약함. 소스에 종속(§0) |
| **Chainlit 스트리밍** | 에이전트 단계·중간 추론을 기본 렌더. job 폴링보다 "과정"이 잘 드러남 |

---

## 3. 설계 원칙

* **Evidence 중심** — 에이전트 수가 아니라, 각 근거가 어떤 가설을 지지/반박하는지 추적되는 구조가 핵심.
* **검색 ≠ 분석 ≠ 판단** — 검색=Retrieval(tool), 분석=②③④⑤⑥, 판단=⑦.
* **반대 근거 우선** — 가장 위험한 실패는 "좋아 보이는 근거만 모으는 것". Critic은 반대 근거를 먼저 찾는다.
* **verdict가 아니라 근거 surfacing** — 1면은 배지가 아니라 가설별 찬반 충돌 보드.

---

## 4. 데이터 소스 전략

| 소스 | 등급 | 수집 | 비고 |
|---|---|---|---|
| 특허 벌크(USPTO 또는 KIPRIS) | Primary (clean) | 공식, 관련 CPC/IPC만 선별 | 시그니처(청구항 중첩)의 근거 |
| 시드 코퍼스(경쟁사·가격·리뷰 샘플) | Tier 1 | 사전 큐레이션 | "준비된 데이터셋"으로 정직 표기 |
| 시장 뉴스·트렌드 | Tier 2 | 검색 API 소량 | rate-limited |
| 라이브 freshness | Tier 3(선택) | 검색 API | 천장 기능 |
| ~~G2·Reddit·실시간 pricing 크롤링~~ | **제거** | — | ToS·anti-bot 지옥 |

**저장(단순)** — 원본 파일은 S3 버킷, 파싱·정제 텍스트 + 메타(출처·신뢰도·수집일·stance)는 PostgreSQL. 메달리온 없음.

---

## 5. 기술 스택 — 각 기술이 "언제" 필요한가

| 기술 | 어떤 상황 | 역할 |
|---|---|---|
| 수집기(Python) | 외부 데이터 가져와 파싱·정제 | 특허·시드·검색 수집 |
| 스케줄러(cron) | 수집/임베딩 주기 갱신 | 가벼운 정기 실행(Airflow는 꼭 필요할 때만) |
| S3(단순 버킷) | 원본 파일 싸게 보관 | 원본 저장(메달리온 아님) |
| PostgreSQL | 관계형 운영 데이터 | 아이디어·가설·근거·특허·로그·판단 |
| **pgvector** | Postgres 안에서 임베딩 검색 | 의미 기반 근거 검색 |
| **tsvector** | 정확한 키워드(lexical) 검색 | 청구항·경쟁사명 매칭 |
| **임베딩 모델** | 청구항·기술요소 벡터화 | **PatentSBERTa / KorPatBERT**(§0 소스 종속), 512토큰 청크+평균풀링 |
| Bedrock Converse | AWS 안 일관 LLM 호출 | 에이전트 LLM |
| LangGraph | 상태 기반 멀티에이전트(분기·Critic) | ①~⑦ 워크플로우 |
| FastAPI | 비동기 job + **스트리밍(SSE)** | job 생성·상태·진행 이벤트 |
| **Chainlit** | 에이전트 단계·스트리밍 데모 | Evidence Board UI(폴백 Streamlit) |
| Secrets Manager + 로깅 | 비밀 관리·실행 로그 | API key·운영 로그 |

> **뺀 것**: 메달리온 레이크·Glue·Athena·dbt·OpenSearch·MWAA·Chroma(별도 벡터DB). 이 규모엔 과설계. 검색·근거·운영을 Postgres 하나로.

---

## 6. 검증 프레임워크: Hypothesis Ledger

| ID | 가설 | 검증 축 | 1차 근거원 | Tier 0 깊이 |
|---|---|---|---|---|
| H1 | 타깃 고객은 회의 후 정리 문제를 자주 겪는다 | 고객 문제 | 시드 리뷰 | **풀(②)** |
| H2 | 기존 도구에 불만 존재 | 경쟁/대체재 | 시드 경쟁사 | 경량(③) |
| H3 | 비용 지불 의향 있다 | 수익모델 | 시드 pricing | 경량(⑥) |
| H4 | 핵심 기능 현재 기술로 구현 가능 | 기술 | 기술 시드 | 경량(④) |
| H5 | 기존 청구항과 직접 중첩 않는 경로 있다 | IP | 특허 벌크 | **풀(⑤, 시그니처)** |

각 가설은 supporting/contradicting evidence, confidence, next_validation_action을 갖고, 에이전트는 **evidence_id로 그라운딩된 구조화 JSON**으로만 결론.

---

## 7. Hybrid Retrieval

```text
가설별 키워드 분해
 → lexical(tsvector) + vector(pgvector) + reliability/freshness 필터
 → rerank: relevance · reliability · freshness · contradiction_value
 → supporting / contradicting / neutral 선택
```

신뢰도 가중: 특허 벌크 高 · 시드 中 · 라이브 검색 低.

---

## 8. 멀티 에이전트 설계 (7) — 풀/경량 + 소유

에이전트는 **LLM 판정·서술만** 한다. 검색·파싱·임베딩은 tool/파이프라인이 끝내놓고, 에이전트는 **벡터DB·PostgreSQL에서 읽어와 판단**할 뿐(검색≠분석의 구현).

| # | 에이전트 | 역할 | Tier 0 깊이 | 소유 |
|---|---|---|---|---|
| ① | Idea Structuring | 아이디어→가설·고객/문제/기술요소/BM 분해 | 전처리(항상) | **C** |
| ② | Market & Customer | 고객 문제·수요·구매 가능성 | **풀**(폭 증명) | **B** |
| ③ | Competitor & Substitute | 경쟁사·대체재·차별화 갭 | 경량→T1 풀 | **B** |
| ④ | Tech (feasibility) | 구현 가능성·필요 데이터/모델/인프라 | 경량 | **C** |
| ⑤ | **IP (청구항 중첩, 시그니처)** | 요소별 중첩 신호·design-around | **풀**(깊이 증명) | **C** |
| ⑥ | Business Model | BM·가격·비용·GTM | 경량→T1 풀 | **D** |
| ⑦ | **Critic & Experiment** | 낙관편향 제거·판단·다음 실험 | 항상(core) | **C** |

> ④ Tech / ⑤ IP는 VentureScout의 ④ Tech&IP를 분리한 것. 시그니처(중첩)는 ⑤에 집중, ④는 경량 유지(Tier 1에서 보강).

### "경량"의 정의 (중요)

경량 ≠ 근거 없는 한 줄(그건 Critic이 쳐낼 overclaim, 폐기). 경량 = **seed 검색 + evidence_id 묶음 + Low confidence + next_experiment**. 분석을 1단계로 줄일 뿐 근거는 유지. confidence가 자연히 Low로 깔려 정직하고, ⑦ Critic의 적대검증 먹잇감이 됨.

```
[가짜 경량 - 금지]  "경쟁 치열함"(출처 없음)
[진짜 경량 - 권장]  경쟁: seed 3건 매칭 → 갭 신호 중간
                   근거: [ev_compet_002, ev_compet_007]
                   confidence: Low (seed 한정) / next: vertical 밀도 조사
```

### 멀티에이전트 정당성 (면접 답변)

> ⑦ Critic이 ②③④⑤⑥의 evidence_id 없는 claim·단정 표현을 반박해 **overclaim rate X%, ungrounded claim Y% 감소**(§11). → Critic ON/OFF 비교가 "왜 멀티에이전트인가"의 근거.

---

## 9. 에이전트별 입출력

| 에이전트 | 입력 | 산출 | 주의점 |
|---|---|---|---|
| ① Structuring | 사용자 아이디어 | idea_type, 고객/문제/솔루션/기술요소, BM 가설, ledger | 유형 오분류 시 후속 기준 붕괴 |
| ② Market | 시드 리뷰·검색 | pain_signal, demand_signal, willingness_to_pay | 불만 ≠ 지불 의향, 출처 없는 TAM 금지 |
| ③ Competitor | 시드 경쟁사·가격 | competitor_matrix(경량: 갭 신호), substitute_map | "경쟁사 없음" 금지 |
| ④ Tech | 기술 시드 | feasibility, tech_risk | 경량 — 신호 수준 |
| ⑤ **IP** | **검색 tool로 읽은 후보 청구항** | feasibility, **ip_overlap_signal**, design_around | 법적 침해 판단 금지 → "중첩 신호" |
| ⑥ Business Model | 시드 pricing·BM | revenue_model, pricing_hypothesis, cost_risk | 수요와 지불의향 혼동 금지 |
| ⑦ Critic | ②~⑥ 출력 + evidence | objections, decision, next_experiments | ungrounded·overclaim 차단, Go 남발 금지 |

> ⑦은 Critic(반박)+Judge(판단) 겸. 스코프상 통합이나, 반박 근거와 최종 신호의 논리적 일치를 리포트에 명시. 필요 시 "반박 Critic / 중립 Decision" 두 노드로 쪼개도 같은 소유(C)라 분담 안 꼬임.

---

## 10. 시그니처: 청구항 중첩 분석 (⑤ IP)

**기계(파이프라인/tool)와 판정(에이전트)이 evidence_id로 분리됨.** ⑤ 에이전트는 기계를 갖지 않고, A·B가 DB에 적재·인덱싱한 결과를 **읽어와 판정만** 한다.

```text
[기계 — A/B, LLM 없음]
 특허 hybrid 검색(pgvector+tsvector+CPC/IPC 필터)   ← B(검색·임베딩)
 → 독립항 추출 → claim limitation 분해             ← A(파싱·적재)
 → 기술요소 ↔ limitation 임베딩 매핑 → evidence_id ← B(임베딩)
 → {evidence_id, limitation, similarity} 적재

[판정 — C(⑤ 에이전트), LLM]
 위 후보를 읽어옴 → 표면유사 vs 실제 중첩 추론
 → overlap_level + design_around (evidence_id 강제 인용)
```

**표현 기준 (법적 판단 금지)**

| 금지 | 수정 |
|---|---|
| 침해 위험 없다 | 직접 중첩 신호 낮음 |
| 침해한다 | 청구항 중첩 신호 높음 |
| 특허 공백이다 | design-around 후보로 보임 |
| 법적으로 안전 | 법률 전문가 검토 필요 영역 |

---

## 11. 평가 (process 기반)

> 창업 검증은 outcome 정답이 없다. verdict 정확도 대신 *과정* 지표.

| 영역 | 지표 |
|---|---|
| Retrieval(손라벨 30~50건) | Precision@K, Contradiction Coverage, Source Diversity |
| Agent | Groundedness, Overclaim Rate, JSON Validity, Consistency |
| **멀티에이전트 효과** | **Critic ON/OFF — overclaim·ungrounded 감소량 정량화** |
| 시스템 | 평균 분석시간, 노드별 latency, token cost, failure rate |

---

## 12. Guardrail

```text
[금지]  성공 가능성 높다 / 침해 위험 없다 / 경쟁사 없다 / 시장 확실히 크다 / 법적으로 안전
[권장]  현재 근거 기준 Pivot 타당 / 불만은 관찰되나 지불의향 추가검증 / 청구항 중첩 신호 있음 /
        경쟁사 존재, 차별화는 vertical에서 / 수요 신호 있으나 CAC·전환 검증 필요
```

---

## 13. UI: Evidence Board (Chainlit)

1면은 verdict가 아니라 가설별 찬반 충돌 보드. **과정은 Chainlit 스트리밍**으로 에이전트 단계를 실시간 렌더, 결과는 Board로.

| 가설 | 찬성 | 반대 | contradiction | 신뢰도 | 다음 검증 |
|---|---|---|---|---|---|
| 회의록 자동화에 돈 낸다 | 유료 경쟁 다수 | 무료/번들 확대 | 높음 | Medium | 가격 인터뷰 |
| 범용 시장 진입 가능 | 수요 존재 | 경쟁 과밀 | 높음 | Low | vertical 조사 |
| IP 중첩 낮음 | 일부 차별 | STT+요약 청구항 밀집 | 중간 | Medium | design-around |

화면: 입력 → 구조화 → 가설 → **Evidence Board** → 경쟁사 매트릭스 → 기술/IP 카드 → Critic 반박 → Go/Pivot/Kill 신호(부산물) → 다음 실험.

> **얇은 클라이언트 원칙** — 에이전트 로직은 FastAPI에, Chainlit은 호출+렌더만. D3 게이트에서 Chainlit 스트리밍이 막히면 Streamlit으로 저비용 후퇴.

---

## 14. 4인 역할 분담

기능이 아니라 **책임 경계**로 나눔. Day 1에 데이터/출력 스키마를 코드로 합의하고 **stub로 4명 동시 착수**(하류 대기 방지). 에이전트는 **State 키 하나만 쓰는 잎**만 분산하고, 척추(State·그래프·⑦·그라운딩)는 C가 단독 소유.

### A — 데이터 수집 & 저장 (에이전트 0, 인프라 헤비)
* 담당: (소스별) 특허 벌크/KIPRIS/BigQuery 수집, 시드 정제, S3 보관, PostgreSQL 적재
* 산출물: 수집·파싱 스크립트, 시드 로더, `patents`·`claims`·`evidence_items` 적재, 수집 스케줄
* **시그니처 기여**: 독립항 추출·**claim limitation 분해**(= 적재 작업의 연장, ⑤ 기계의 파싱부)
* self-eval: 적재 누락·중복률, 메타 완전성, 수집 재현성
* **후반 재배치**: D5쯤 수집 종료 → **D6부터 C의 ④⑤ 프롬프트·평가 케이스 보조**

### B — 검색 & 임베딩 (②③ 에이전트)
* 담당: chunking, **임베딩(모델 결정 PatentSBERTa/KorPatBERT)**, pgvector+tsvector 하이브리드, rerank, 검색 tool 제공
* 에이전트: ② Market(풀), ③ Competitor(경량)
* 산출물: 임베딩 파이프라인, 검색 인덱스, 하이브리드+rerank, `vector_search`·`get_patent_detail` tool, ⑤가 읽을 `{evidence_id, limitation, similarity}` 후보 생성
* self-eval: Precision@K, contradiction coverage, 검색 latency

### C — 에이전트 플랫폼 (①④⑤⑦, 척추)
* 담당: LangGraph 그래프, **State 스키마·출력 pydantic·few-shot·가드레일 중앙 정의·배포**, evidence_id 그라운딩 강제
* 에이전트: ① Structuring, ④ Tech(경량), ⑤ **IP(풀, 시그니처 판정)**, ⑦ **Critic(척추)** — 모두 **읽기+LLM 판정**, 기계 없음
* 산출물: 7노드 그래프, 그라운딩 로직, 프롬프트 버전 관리
* **부하 특성**: 코드량 아닌 **난도**로 무거움(⑤+⑦). 단일 병목 방어 = Day 1 스키마 계약 분리 + A의 D6 보조
* self-eval: groundedness, overclaim rate, JSON validity, Critic ON/OFF 효과
* **요건**: 팀에서 프롬프트·에이전트 가장 센 사람

### D — 백엔드 · UI · 평가 (⑥ 에이전트, 팀장 자리)
* 담당: FastAPI 비동기 job+스트리밍, Chainlit Evidence Board, **평가 하네스/대시보드**
* 에이전트: ⑥ Business Model(경량)
* 산출물: job API, Board·리포트 화면, **평가 대시보드(Critic ON/OFF 정량화)**, 인터페이스 계약·전체 통합
* self-eval: E2E 시연 안정성, 평가 완성도, "멀티에이전트 효과" 증명 강도

### 부하 곡선 (피크 어긋남으로 균형)
* A 전반(D1~5) 피크 / B 중반 / C 전 구간 / D 후반(D6~9) 피크
* A의 후반 여유 → C의 후반 피크에 투입(D6~ 보조)로 평준화. **C만 구조적 풀로드** → 척추 외 부담을 A·B·D가 감쌈.

---

## 15. 개발 순서 + Tier 로드맵

> **규율: Tier 0가 E2E로 돌기 전까지 Tier 1+ 금지.**

* **Phase 0 (D1)** — 데이터/State 스키마 코드 합의(§3 TypedDict 계약), 4 레이어 stub 배포 → 병렬 시작. **데이터 소스 확정(§0)**.
* **Tier 0 (MVP·비협상)** — 특허 일부 + 시드 최소 적재 · hybrid retrieval 최소 · **⑤ IP(풀) + ② Market(풀)** · ③④⑥ 경량 신호 · ⑦ Critic · Evidence Board **E2E**
* **Tier 1** — ③ Competitor · ⑥ BM 풀 승격 · ④ 보강 · rerank 고도화
* **Tier 2** — 평가 하네스(§11 전체 + Critic ON/OFF)
* **Tier 3 (천장)** — 라이브 freshness · **음성/녹음 전사 입력**(⓪ 추출 노드 + 사용자 확인 스텝) · 산업군 분기

> **입력 정책**: Tier 0는 **계획서형 텍스트**만(① 신뢰도 확보, 그라운딩 본질 집중). 전사·녹음은 추정이 끼어 그라운딩을 흔들므로, ① 앞에 ⓪ 추출·정제 노드 + 사용자 확인을 두고 **Tier 3로** 미룸.

---

## 16. 강점 / Risk

| 강점 | 설명 |
|---|---|
| 적정 스펙 | Postgres 중심 과설계 회피 → 4인 완주 |
| 시그니처 명확 | 청구항 중첩 신호(clean 데이터 위) + **특허 특화 임베딩 명시** |
| 멀티에이전트 증명 | Critic ON/OFF 정량화 |
| 평가 정직 | outcome 아닌 process |
| 보너스 DE | 수집·정제·검색 적재 |
| 혼합 스코프 | 넓은 보드 + IP 깊이, 경량도 근거 묶여 "죽은 칸" 없음 |

| Risk | 대응 |
|---|---|
| LLM 리포트로 보임 | evidence_id 그라운딩 + Critic |
| 데이터 수집 탈진 | 적대 소스 제거, 특허 선별 + 시드 한정 |
| IP 과대주장 | 법적 판단 금지, 신호 표현 강제 |
| 스코프 과대 | Tier 0 하드 게이트 |
| C 단일 병목 | Day 1 스키마 계약 분리, A의 D6 보조, 잎만 분산 |
| 경량 칸이 빈약 | seed 검색+evidence_id+Low confidence, ⑤ 풀로 깊이 증명 |
| 임베딩 승인 지연(KIPRIS) | 사전 신청 + 폴백 체인(§0) |
| Chainlit 스트리밍 실패 | 얇은 클라이언트 → Streamlit 후퇴 |
| 팀 순차 의존 | Day 1 스키마 합의, stub 병렬 착수 |

---

## 17. 최종 요약

| 항목 | 설계 |
|---|---|
| 본체 | 창업 아이디어 evidence surfacing + 적대 검증 |
| 데이터 소스 | **영어(USPTO/BigQuery) / 한국어(KIPRIS) — Day 1 결정(§0)** |
| 데이터 | 특허 선별 + 시드 코퍼스, PostgreSQL 적재(메달리온 없음) |
| 시그니처 | 청구항 중첩 신호(⑤ IP, 풀) |
| 임베딩 | PatentSBERTa(영어) / KorPatBERT(한국어), 512 청크+평균풀링 |
| 검색 | lexical(tsvector) + vector(pgvector) hybrid + rerank |
| 에이전트 | ① + ②③④⑤⑥ 분석 + ⑦ Critic (7), 읽기+판정만 |
| 스코프 | **혼합** — ②⑤ 풀, ③④⑥ 경량(근거 묶임) |
| 평가 | process 기반(groundedness·precision·overclaim·Critic ON/OFF) |
| 1면 | Evidence Board(Chainlit, verdict는 부산물) |
| 스택 | Python 수집 · S3 · PostgreSQL(+pgvector/tsvector) · Bedrock · LangGraph · FastAPI(스트리밍) · Chainlit |
| 분담 | A 파이프라인 / B 검색·임베딩+②③ / C 플랫폼+①④⑤⑦ / D 백엔드·UI·평가+⑥ |
| 규율 | Tier 0 E2E 하드게이트, Day 1 스키마 합의, stub 병렬 착수 |
| 기간/인원 | 10일 / 4인 |
| 3대 핵심 리스크 | 데이터 소스 결정 / Day 1 스키마 합의 / C 병목 |

---

## 18. 미결정 & 참고

**남은 Day 1 결정**: 데이터 소스(영어/한국어) — §0. 정해지면 임베딩·분류코드·언어가 자동 확정.

**참고 공식 문서**
* USPTO Open Data Portal: https://data.uspto.gov/
* USPTO Bulk Data: https://data.uspto.gov/bulkdata/datasets
* KIPRIS Plus(한국어 옵션 시): https://plus.kipris.or.kr/
* Amazon Bedrock Converse API: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
* pgvector: https://github.com/pgvector/pgvector
* LangGraph: https://langchain-ai.github.io/langgraph/
* PatentSBERTa: https://huggingface.co/AI-Growth-Lab/PatentSBERTa
