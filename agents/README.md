# Track C - Agent Platform

VentureScout Track C는 사용자의 창업 아이디어를 가설로 구조화하고, 근거 기반 분석을 거쳐 최종 판단과 다음 검증 과제를 만드는 에이전트 workflow다.

## 전체 흐름

```text
문서/PDF/DOCX
  -> 텍스트 추출
  -> raw_input
  -> Structuring
  -> Market / Competitor / BM / Tech / IP 병렬 분석
  -> Critic
  -> decision + final_report
```

Track C 담당 에이전트:

```text
① Structuring
④ Tech(light)
⑤ IP(full)
⑦ Critic
```

## 에이전트 입출력

| 에이전트 | 주요 입력 | 주요 출력 |
|---|---|---|
| Structuring | `raw_input` | 아이디어 구조화 값, 가설, 기술요소, 특허 키워드 |
| Market | H1, H1 근거 | 고객 문제 분석 `AgentRun` |
| Competitor | H2, H2 근거 | 경쟁·차별화 분석 `AgentRun` |
| BM | H3, H3 근거 | 가격·수익모델 분석 `AgentRun` |
| Tech | H4 근거, `technical_elements` | 구현 가능성·비용·지연·보안 분석 |
| IP | H5 근거, 기술요소, `ip_overlap_candidates` | 특허 중첩 신호·회피 설계 검토 |
| Critic | 모든 분석 결과와 근거 | `decision`, 반론, 다음 실험, 최종 보고서 |

병렬 분석 에이전트는 Structuring이 만든 같은 State를 받는다. 각 에이전트는 그중 담당 가설과 근거만 골라 사용한다.

```text
공통 State
├─ job_id / idea_id
├─ raw_input / idea
├─ hypotheses
├─ documents
├─ evidence_items
└─ technical_elements

Market      -> H1 evidence
Competitor  -> H2 evidence
BM          -> H3 evidence
Tech        -> H4 evidence + technical_elements
IP          -> H5 evidence + technical_elements + ip_overlap_candidates
```

IP만 별도의 State를 받는 것은 아니다. 공통 State에 특허 limitation 중첩 후보를 추가로 사용하는 차이가 있다.

## 입력 형식

사용자는 기본적으로 `raw_input`만 제공하면 된다.

```python
state["raw_input"] = "사업계획서에서 추출한 전체 텍스트"
```

`raw_input`은 파일 경로나 파일 객체가 아니라 **문서에서 추출한 텍스트**다.

```text
파일 업로드
  -> 파일 파서
  -> 전체 텍스트
  -> ideas.raw_input
  -> Structuring 실행
```

Claude가 연결된 경우 자연어 문서에서 필요한 값을 추출한다. Claude를 사용하지 못할 때는 다음과 같은 라벨 형식이 가장 안정적이다.

```text
제목: 음식점 재고 예측 서비스
타깃 고객: 소규모 음식점
문제: 식자재 폐기가 반복된다
해결책: 판매량과 유통기한으로 재고를 예측한다
비즈니스 모델: 매장 단위 월 구독
기술 요소: 수요 예측, 시계열 분석
특허 키워드: inventory forecasting, demand prediction
```

## 공통 출력

분석 에이전트는 공통 envelope를 사용한다.

```text
AgentRun
├─ agent_name
├─ hypothesis_id
├─ depth
├─ confidence
├─ grounded_on[]
└─ output_json
```

가장 중요한 연결:

```text
agent_runs.grounded_on
  -> evidence_items.evidence_id
```

에이전트의 주장은 실제 `evidence_id`를 인용해야 하며, Critic은 근거 누락과 과장 표현을 검사한다.

## 최종 보고서

Critic은 짧은 decision만 내리지 않고, 수집된 근거를 운영용 보고서로 정리한다.

```text
final_report
├─ executive_summary          최종 판단과 핵심 이유
├─ idea_snapshot              고객·문제·해결책·기술요소
├─ hypothesis_assessment      H1~H5별 confidence와 인용 근거
├─ related_patents            특허번호·제목·중첩 요소·점수·검토 이유
├─ related_business_signals   경쟁·고객·가격 관련 문서와 제안
├─ strategic_options          진행·축소·피벗 선택지
├─ priority_recommendations   우선순위·이유·성공 기준
├─ research_gaps              아직 부족한 검색/근거
└─ traceability               evidence_id와 decision rule
```

관련 특허와 사업 신호는 현재 `documents/evidence_items`에서 확인된 후보만 제시한다. 근거가 없으면 회사명이나 특허번호를 생성하지 않고 `research_gaps`에 추가 수집 과제로 기록한다.

DB mode에서는 보고서 표시를 위해 evidence가 가리키는 `documents`의 제목, 외부번호, URL을 읽기 전용으로 보강한다. DB 스키마와 contract는 변경하지 않는다.

## Track C 상세 노드

`agents/nodes/*`의 하드코딩된 회의록 SaaS 전용 문구는 제거했다.

```text
agents/nodes/structuring_node.py
  raw_input을 Claude 또는 보수적인 라벨 파서로 구조화한다.

agents/nodes/tech_node.py
  State의 실제 H4 evidence와 기술요소를 우선 사용한다.

agents/nodes/ip_node.py
  State의 실제 IP 후보와 hybrid_score를 사용한다.

agents/nodes/critic_node.py
  근거 누락, confidence, 과장, IP 신호로 판단한다.

agents/nodes/runtime_inputs.py
  기존 State/Agent 출력 구조를 바꾸지 않고 실제 입력을 읽는 보조 계층이다.
```

기존 상세 노드의 외부 데이터 구조는 유지한다.

```text
Structuring 가설: H1 / H4 / H5
Tech 저장 위치: state["tech_result"]
IP 저장 위치: state["ip_result"]
Critic 저장 위치: state["critic_result"], decision, final_report
```

현재 실제 LangGraph는 `agents/graph.py` 안의 노드 함수를 사용한다. `agents/nodes/*`는 상세 노드 호환 구현이며, 향후 graph에 연결해도 특정 아이디어로 덮어쓰지 않도록 범용화한 상태다.

## 파일 역할

```text
agents/graph.py
  현재 실행되는 LangGraph와 decision rule

agents/llm.py
  AWS Bedrock Claude 호출과 fallback 처리

agents/db_workflow.py
  ideas/jobs/hypotheses/evidence/agent run DB 저장 함수

agents/mock_data.py
  로컬 테스트 fixture

agents/mock_repository.py
  상세 노드 단독 테스트용 mock repository

retrieval/tools.py
  mock 또는 DB 검색 결과를 EvidenceItem으로 변환

retrieval/pgvector_search.py
  documents.embedding 기준 pgvector 검색
```

## 실행 모드

### Mock workflow

```powershell
python scripts/run_mock_graph.py
```

AWS와 DB 없이 graph 구조를 검증한다.

### Bedrock Claude

```powershell
python scripts/run_bedrock_graph.py
```

`run_bedrock_graph.py`는 `AGENT_LLM_PROVIDER=bedrock`을 자동으로 설정한다. 단, AWS 인증이나 모델 권한 문제가 생기면 graph를 중단하지 않고 fallback 결과를 사용한다.

확인 기준:

```text
Model: mock
  -> Claude 미사용

Model: bedrock:anthropic.claude-...
  -> Bedrock Claude 모드

output_json.llm_fallback_used = true
  -> Claude 호출 실패 후 fallback 사용
```

### PostgreSQL workflow

```powershell
python scripts/run_db_graph.py
```

실행 과정:

```text
ideas 생성
  -> analysis_jobs 생성
  -> hypotheses 생성
  -> documents 검색
  -> evidence_items 저장
  -> LangGraph 실행
  -> agent_runs 저장
  -> decision 업데이트
```

입력을 바꾸려면:

```powershell
$env:VENTURESCOUT_RAW_INPUT="분석할 창업 아이디어 또는 문서 텍스트"
python scripts/run_db_graph.py
```

DB와 Claude를 함께 사용하려면:

```powershell
$env:AGENT_LLM_PROVIDER="bedrock"
python scripts/run_db_graph.py
```

## DB 점검

```powershell
python scripts/inspect_db.py
python scripts/inspect_documents.py
python -m retrieval.pgvector_search
python -m agents.db_workflow
```

`documents` 검색 결과의 `distance`가 작을수록 query vector와 문서 vector가 가깝다. 하지만 현재 query embedding은 deterministic mock vector이므로 검색 품질이나 IP 안전성을 판단하는 용도로 사용하면 안 된다.

## 현재 한계

```text
1. query embedding 생성은 아직 실제 embedding 모델이 아니다.
2. DB mode의 claim_limitations -> ip_overlap_candidates 검색은 연결 전이다.
3. run_db_graph.py의 최초 idea/hypothesis 준비 과정에는 일부 mock fixture가 남아 있다.
4. PDF/DOCX/HWP 파일 파서는 아직 Structuring 앞단에 연결되지 않았다.
5. agents/nodes/*는 아직 agents/graph.py에 직접 연결되지 않았다.
```

## 테스트

```powershell
python -m pytest
```

현재 확인 항목:

```text
공통 contract 검증
mock LangGraph E2E
상세 노드 State/출력 구조 호환
비회의록 아이디어 입력 처리
```

현재 기대 결과:

```text
9 passed
```

## 변경 금지 영역

별도 합의 전에는 다음 스키마와 contract 파일을 수정하지 않는다.

```text
db/init.sql
db/schema.dbml
docs/schema_tier0.md
shared/contracts.py
shared/state.py
agents/schemas.py
agents/state.py
```

## 개발 원칙

- 모든 분석 주장은 `evidence_id`를 인용한다.
- IP Agent는 법적 침해 여부를 단정하지 않는다.
- Critic은 근거 누락, low confidence, overclaim을 우선 검사한다.
- 실제 데이터가 있으면 State/DB 데이터를 우선하고 mock은 테스트 fallback으로만 사용한다.
- 기존 State와 AgentRun 데이터 구조를 유지해 다른 브랜치와의 병합 충돌을 줄인다.
