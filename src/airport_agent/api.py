"""HTTP front end: `POST /chat` runs one question through the agent of a session.

No login: it is meant for one analyst or a demo on a trusted network (see the README).
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException

from airport_agent.agent import build_agent
from airport_agent.answer import Answer, Status
from airport_agent.chat_models import ChatRequest, ChatResponse, NotesOut
from airport_agent.config import DEFAULT_CONFIG_PATH, Settings, load_settings
from airport_agent.data.http import HttpFetcher
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.dataset import Dataset, load_dataset
from airport_agent.env_file import DEFAULT_ENV_PATH, load_env_file
from airport_agent.llm import LlmClient
from airport_agent.sessions import Session, SessionStore

logger = logging.getLogger(__name__)
SECONDS_PER_MINUTE = 60


def _response(session_id: str | None, answer: Answer) -> ChatResponse:
    notes = answer.notes

    return ChatResponse(
        session_id=session_id,
        status=answer.status.value,
        text=answer.text,
        notes=NotesOut(
            assumptions=list(notes.assumptions),
            caveats=list(notes.caveats),
            sources=list(notes.sources),
        ),
    )


def _open_session(store: SessionStore, session_id: str | None) -> tuple[str, Session, bool]:
    """The conversation to use and whether it was just created."""
    if session_id is None:
        new_id, created = store.create()

        return new_id, created, True

    found = store.get(session_id)

    if found is None:
        raise HTTPException(404, "unknown or expired session; start a new one")

    return session_id, found, False


def create_app(settings: Settings, data: Dataset, llm: LlmClient) -> FastAPI:
    app = FastAPI(title="Airport Investment Intelligence Agent")
    store = SessionStore(
        lambda: build_agent(llm, data),
        ttl_seconds=settings.session_ttl_minutes * SECONDS_PER_MINUTE,
        max_sessions=settings.max_sessions,
    )
    app.state.sessions = store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat")
    def chat(request: ChatRequest) -> ChatResponse:
        limit = settings.max_message_chars

        if len(request.message) > limit:
            raise HTTPException(413, f"message is longer than {limit} characters")

        session_id, session, is_new = _open_session(store, request.session_id)

        try:
            with session.lock:
                answer = session.agent.ask(request.message)
        except Exception:
            if is_new:  # the client never learns this id, so the session would sit unused
                store.discard(session_id)

            raise

        if is_new and answer.status is Status.UNAVAILABLE:  # nothing was remembered
            store.discard(session_id)

            return _response(None, answer)

        return _response(session_id, answer)

    return app


def app_from_environment(env_path: Path = DEFAULT_ENV_PATH) -> FastAPI:
    """`uvicorn airport_agent.api:app_from_environment --factory`. Needs ANTHROPIC_API_KEY, from
    the environment or the repo's `.env` (a variable already set wins)."""
    from airport_agent.anthropic_client import AnthropicClient  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO)
    load_env_file(env_path)

    settings = load_settings()
    llm = AnthropicClient.from_settings(settings)  # fails at once when the key is missing
    store = SnapshotStore(DEFAULT_CONFIG_PATH.parent / settings.snapshot_dir)
    data = load_dataset(settings, store, HttpFetcher())
    logger.info("dataset loaded, traffic through %s", data.traffic.provenance.as_of)

    return create_app(settings, data, llm)
