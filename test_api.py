import logging
from livekit.agents import AgentSession, inference
import asyncio

async def test():
    try:
        session = AgentSession(
            stt=inference.STT(),
            llm=inference.LLM(),
            tts=inference.TTS()
        )
        print(f"session object: {session}")
        print(f"Has chat_ctx: {hasattr(session, 'chat_ctx')}")
        print(f"Has chat_context: {hasattr(session, 'chat_context')}")
        print(f"Has generate_reply: {hasattr(session, 'generate_reply')}")
    except Exception as e:
        print(f"Error checking attributes: {e}")

if __name__ == "__main__":
    asyncio.run(test())
