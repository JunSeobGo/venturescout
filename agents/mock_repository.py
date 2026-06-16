"""
개발/테스트 환경에서 사용하는 Mock Repository.

현재 실제 DB 대신 agents.mock_data의 고정 데이터를 반환한다.
상세 노드(agents/nodes/*)가 아직 Repository 인터페이스를 기대하므로
올바른 모듈명인 agents.mock_repository에 구현을 둔다.
"""

from __future__ import annotations

from typing import Any

from agents.mock_data import MOCK_EVIDENCE, MOCK_IP_CANDIDATES


class MockRepository:
    """실제 Repository를 붙이기 전까지 사용하는 mock 구현체."""

    def get_evidence_for_hypothesis(self, hypothesis_id: str) -> list[dict[str, Any]]:
        """가설 ID에 해당하는 evidence_items mock 데이터를 반환한다."""

        return [
            evidence
            for evidence in MOCK_EVIDENCE
            if evidence["hypothesis_id"] == hypothesis_id
        ]

    def get_ip_overlap_candidates(
        self,
        job_id: str,
        hypothesis_id: str,
    ) -> list[dict[str, Any]]:
        """IP 시그니처 후보 mock 데이터를 반환한다."""

        return [
            {
                **candidate,
                "job_id": job_id,
            }
            for candidate in MOCK_IP_CANDIDATES
            if candidate["hypothesis_id"] == hypothesis_id
        ]

    def insert_agent_run(self, payload: dict[str, Any]) -> str:
        """Agent 실행 결과 저장을 흉내 낸다."""

        agent_name = payload.get("agent_name", "unknown")
        print("[MOCK insert_agent_run]", agent_name)
        return f"run_{agent_name}"

    def insert_critic_objections(self, objections: list[dict[str, Any]]) -> None:
        """Critic 반론 저장을 흉내 낸다."""

        print("[MOCK insert_critic_objections]", len(objections))

    def update_job_stage(
        self,
        job_id: str,
        stage: str,
        progress_pct: int,
    ) -> None:
        """Job 진행 상태 업데이트를 흉내 낸다."""

        print(f"[MOCK stage] {job_id} | {stage} | {progress_pct}%")
