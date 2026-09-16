# 검색 정답 라벨셋

`precision_at_k` / `contradiction_coverage`를 계산하기 위한 정답 데이터.
ADR-019/029에서 "정답 라벨셋 필요"로 미뤄져 있던 자리다.

## 왜 필요한가

검색 품질은 `hybrid_score`나 `relevance_score`만으로는 알 수 없다. 그건 **모델이
매긴 점수**일 뿐, "이 쿼리에 이 문서가 실제로 적합한가"는 사람이 판단해야 한다.
라벨 없이 임계값이나 rerank 가중치를 만지면 근거 없는 튜닝이 된다.

## 만드는 법

```bash
# 1. 후보 생성 (DB 접속 필요, LLM은 안 씀)
python -m eval.build_labelset --pool 10

# 2. eval/labels/retrieval_labels.json 을 열어 각 문서의
#    relevant 를 true/false 로 채우고 stance 를 확인한다

# 3. 지표 계산
python -m eval.labelset
```

`--pool`을 k(기본 5)보다 크게 잡는 이유: 상위 5개만 라벨링하면 precision@5가
라벨을 그대로 되돌려주는 순환이 된다. 넉넉히 뽑아야 순위 변화를 관측할 수 있다.

## 포맷

```json
{
  "version": 1,
  "pool": 10,
  "queries": [
    {
      "query_id": "saas-h1",
      "axis": "customer_problem",
      "query": "Teams lose time because maintenance knowledge is scattered across tools.",
      "source_types": ["seed_review"],
      "labels": {
        "<document_id>": {
          "relevant": true,
          "stance": "supports",
          "_source_type": "seed_review",
          "_excerpt": "...",
          "_relevance_score": 0.4213
        }
      }
    }
  ]
}
```

| 필드 | 채우는 주체 | 의미 |
|---|---|---|
| `relevant` | **사람** | 이 쿼리에 이 문서가 적합한가 (`true` / `false`). `null`이면 미라벨 |
| `stance` | **사람** (초기값은 검색이 제안) | `supports` / `contradicts` / `neutral` |
| `_`로 시작하는 키 | 스크립트 | 판단 참고용. 지표 계산에 쓰이지 않음 |

## 라벨링 기준

**relevant = true** — 이 문서를 읽으면 해당 가설을 지지하든 반박하든 **판단에
보탬이 되는가**. 같은 도메인이라는 이유만으로 true를 주지 않는다.

**stance** — 문서 내용이 가설에 대해:
- `supports`: 가설이 맞다는 쪽 신호
- `contradicts`: 가설이 틀렸다는 쪽 신호 ← **이걸 정확히 다는 게 중요하다**
- `neutral`: 관련은 있으나 방향성 없음

`contradicts` 라벨이 `contradiction_coverage`의 분모가 된다. Evidence Board가
"상충 근거를 드러낸다"고 주장하려면 이 지표가 받쳐줘야 하므로, 반박 문서를
빠뜨리지 않는 게 이 라벨셋의 핵심이다.

## 주의

- 이 파일은 **커밋한다.** 재현 가능한 평가의 전제다
- 시드 데이터가 바뀌면 `document_id`가 달라져 라벨이 무효가 된다.
  재적재 후에는 라벨셋도 다시 만들어야 한다
- `unlabeled_in_topk`가 크면 precision이 과소평가된 것이다. 라벨을 더 채워라
