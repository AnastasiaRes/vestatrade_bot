from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.agents.orchestrator import ChatOrchestrator
from app.chat_logger import ChatLogger
from app.config import get_settings
from app.models import ChatRequest, ChatResponse


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
orchestrator = ChatOrchestrator()
chat_logger = ChatLogger(settings.chat_logs_dir)
STATIC_DIR = Path(__file__).resolve().parent / "static"
# Feed replacement is process-wide. ChatOrchestrator itself isolates mutable
# request agents per worker thread and serializes only turns of the same session.
_feed_reload_lock = RLock()


def startup_load_feed() -> None:
    try:
        with _feed_reload_lock:
            count, source = orchestrator.reload_products(refresh=True)
        logger.info("Loaded %s products from %s on startup", count, source)
    except Exception as exc:
        logger.warning("Startup feed load failed, bot will try cache on demand: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_load_feed()
    yield


app = FastAPI(
    title="Vesta Trading AI Consultant",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "products_loaded": len(orchestrator.search_agent.products),
        "products_loaded_from": orchestrator.products_loaded_from,
        "product_docs_loaded": orchestrator.docs_attached,
        "llm_provider": settings.llm_provider,
        "llm_configured": settings.llm_enabled,
        "llm_model": settings.llm_model if settings.llm_enabled else None,
        "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
        "llm_attempt_timeout_seconds": (
            settings.llm_request_timeout_seconds
            if settings.llm_provider == "ollama"
            else min(
                settings.llm_timeout_seconds,
                settings.llm_request_timeout_seconds,
            )
        ),
        "llm_max_retries": settings.llm_max_retries,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/styles.css")
async def root_styles() -> FileResponse:
    return FileResponse(STATIC_DIR / "styles.css")


@app.get("/app.js")
async def root_script() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.js")


@app.get("/widget-loader.js")
async def widget_loader() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "widget-loader.js",
        media_type="application/javascript",
    )


@app.get("/widget-demo")
async def widget_demo() -> FileResponse:
    return FileResponse(STATIC_DIR / "widget-demo.html")


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    # The orchestration path intentionally waits for a local LLM for up to the
    # shared request budget.  Keep that blocking work outside the ASGI event
    # loop so /health and static pages stay responsive meanwhile.
    response = await run_in_threadpool(
        orchestrator.handle_chat,
        request.session_id,
        request.message,
    )
    await run_in_threadpool(
        chat_logger.log_turn,
        request.session_id,
        request.message,
        response,
    )
    return response


@app.post("/reload-feed")
def reload_feed(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    if settings.reload_feed_token and x_admin_token != settings.reload_feed_token:
        raise HTTPException(status_code=403, detail="invalid reload token")
    try:
        with _feed_reload_lock:
            count, source = orchestrator.reload_products(refresh=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "products_count": count, "source": source}
