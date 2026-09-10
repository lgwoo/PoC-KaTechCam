"""FastAPI 진입점.

실행: uvicorn app.main:app --reload --port 8000   (server/ 디렉터리에서)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import routes_session
from app.config import load_settings
from db import repo
from llm.client import LlmClient
from safety.moderation import ModerationClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    conn = repo.connect(settings.db_path)
    client = LlmClient(
        api_key=settings.elice_api_key,
        base_url=settings.elice_base_url,
        model=settings.elice_model,
        max_tokens=settings.max_output_tokens,
    )
    moderation = ModerationClient(
        api_key=settings.moderation_api_key, model=settings.moderation_model
    )
    routes_session.configure(conn=conn, client=client, moderation=moderation)

    logger.info("model=%s db=%s", settings.elice_model, settings.db_path)
    if not settings.moderation_available:
        # 이 상태로도 돌긴 하지만 방어망 한 겹이 빠진 것이다. 조용히 넘어가면 안 된다.
        logger.warning("OPENAI_MODERATION_API_KEY 미설정 — Moderation 없이 실행한다")

    yield
    conn.close()


app = FastAPI(title="느링고 역할극 PoC", lifespan=lifespan)

# Vite 개발 서버에서 부른다. PoC 로컬 전용.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_session.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
