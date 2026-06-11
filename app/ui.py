"""
Track D — Chainlit Evidence Board (얇은 클라이언트).
FastAPI 스트리밍을 받아 에이전트 단계를 cl.Step으로 렌더 → 결과는 Board로.
폴백: D3 게이트에서 막히면 Streamlit으로 교체(뷰 레이어만).
"""
import chainlit as cl

# TODO(D): FastAPI /analyze SSE 구독 → cl.Step 단계 표시 → Evidence Board 렌더


@cl.on_message
async def main(msg: cl.Message):
    async with cl.Step(name="① 구조화") as s:
        s.output = "아이디어를 가설로 분해 (mock)"
    async with cl.Step(name="⑤ IP 청구항 중첩 (시그니처)") as s:
        s.output = "청구항 중첩 신호 분석 (mock)"
    async with cl.Step(name="⑦ Critic") as s:
        s.output = "적대 검증 → more_research (mock)"
    await cl.Message(content="**Evidence Board** (mock)\n\n결론: More Research").send()
