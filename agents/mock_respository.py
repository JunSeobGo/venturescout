"""
agents/mock_repository.py

개발 및 테스트 환경에서 사용하는 Mock Repository.

역할:
1. 실제 DB 대신 가짜 데이터 제공
2. Agent가 필요한 Evidence 조회
3. IP 중복 후보 조회
4. Agent 실행 결과 저장 시뮬레이션
5. Critic 결과 저장 시뮬레이션
6. Job 진행상태 업데이트 시뮬레이션

실서비스에서는 PostgreSQL, VectorDB, Elasticsearch,
Patent DB 등을 조회하게 되지만,
현재는 mock_data.py의 고정 데이터를 반환한다.

즉, Repository 계층 인터페이스를 미리 만들어두고
나중에 실제 DB 구현체로 교체하기 쉽게 하기 위한 목적이다.
"""

from agents.mock_data import MOCK_EVIDENCE, MOCK_IP_CANDIDATES


class MockRepository:
    """
    테스트용 Repository 구현체

    실제 Repository 대신 사용되며
    미리 정의된 Mock 데이터를 반환한다.
    """

    def get_evidence_for_hypothesis(self, hypothesis_id: str) -> list[dict]:
        """
        가설(Hypothesis)에 해당하는 Evidence 조회

        예:
        H4 → 기술성 관련 증거
        H5 → IP/특허 관련 증거

        실제 서비스:
        SELECT * FROM evidence
        WHERE hypothesis_id = ?
        """

        # 기술성 가설
        if hypothesis_id == "H4":
            return [
                e
                for e in MOCK_EVIDENCE
                if e["evidence_id"].startswith("ev_tech")
            ]

        # IP 가설
        if hypothesis_id == "H5":
            return [
                e
                for e in MOCK_EVIDENCE
                if e["evidence_id"].startswith("ev_ip")
            ]

        # 그 외 가설은 일부 기본 데이터 반환
        return MOCK_EVIDENCE[:2]

    def get_ip_overlap_candidates(
        self,
        job_id: str,
        hypothesis_id: str
    ) -> list[dict]:
        """
        특허 중복 후보 조회

        실제 서비스:
        1. 기술 요소 임베딩 생성
        2. Vector Search
        3. Patent Claim 검색
        4. Hybrid Score 계산
        5. Top-K 후보 반환

        현재는 Mock 데이터 반환
        """

        return MOCK_IP_CANDIDATES

    def insert_agent_run(self, payload: dict) -> str:
        """
        Agent 실행 결과 저장

        실제 서비스:
        INSERT INTO agent_runs (...)

        현재는 콘솔 출력만 수행
        """

        print("[MOCK insert_agent_run]", payload["agent_name"])

        # 가짜 Run ID 반환
        return f"run_{payload['agent_name']}"

    def insert_critic_objections(
        self,
        objections: list[dict]
    ) -> None:
        """
        Critic Agent의 반론 저장

        실제 서비스:
        INSERT INTO critic_objections (...)

        현재는 저장 대신 로그 출력
        """

        print(
            "[MOCK insert_critic_objections]",
            len(objections)
        )

    def update_job_stage(
        self,
        job_id: str,
        stage: str,
        progress_pct: int
    ) -> None:
        """
        Job 진행상태 업데이트

        예:
        parsing → 10%
        market_analysis → 40%
        tech_analysis → 70%
        completed → 100%

        실제 서비스:
        UPDATE jobs
        SET stage=?, progress_pct=?

        현재는 로그 출력만 수행
        """

        print(
            f"[MOCK stage] "
            f"{job_id} | {stage} | {progress_pct}%"
        )