from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import secrets
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
        logger.warning(
            "Startup feed load failed; bot will try cache on demand error_type=%s",
            type(exc).__name__,
        )


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


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Return a stable error contract without exposing implementation details."""

    trace_id = uuid4().hex
    # Exception strings from HTTP/Redis clients can contain credential-bearing
    # URLs.  Keep correlation and type, but never serialize the raw exception.
    logger.error(
        "Unhandled API error trace_id=%s method=%s path=%s error_type=%s",
        trace_id,
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Не удалось обработать запрос. Повторите попытку позже.",
                "trace_id": trace_id,
            }
        },
    )


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


@app.get("/ready")
async def ready() -> JSONResponse:
    products_loaded = len(orchestrator.search_agent.products)
    is_ready = products_loaded > 0
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "status": "ready" if is_ready else "not_ready",
            "products_loaded": products_loaded,
            "products_loaded_from": orchestrator.products_loaded_from,
        },
    )


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
async def chat(
    request: ChatRequest,
    x_dialogue_qa_token: str | None = Header(
        default=None,
        alias="X-Dialogue-QA-Token",
    ),
) -> ChatResponse:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    if request.qa_mode is not None:
        configured_token = settings.dialogue_v2_qa_control_token
        qa_authorized = bool(
            settings.dialogue_v2_qa_controls_enabled
            and configured_token
            and x_dialogue_qa_token
            and secrets.compare_digest(configured_token, x_dialogue_qa_token)
        )
        if not qa_authorized:
            raise HTTPException(status_code=403, detail="QA dialogue mode is disabled")
    # The orchestration path intentionally waits for a local LLM for up to the
    # shared request budget.  Keep that blocking work outside the ASGI event
    # loop so /health and static pages stay responsive meanwhile.
    if request.qa_mode is not None:
        response = await run_in_threadpool(
            orchestrator.handle_chat,
            request.session_id,
            request.message,
            request.client_turn_id,
            request.qa_mode,
        )
    elif request.client_turn_id is None:
        # Preserve the original two-argument controller boundary for existing
        # integrations and tests.  The optional retry key is forwarded only
        # when the client actually supplies it.
        response = await run_in_threadpool(
            orchestrator.handle_chat,
            request.session_id,
            request.message,
        )
    else:
        response = await run_in_threadpool(
            orchestrator.handle_chat,
            request.session_id,
            request.message,
            request.client_turn_id,
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
    # Reload mutates process-wide catalog state and may initiate an external
    # fetch.  A missing server-side token therefore disables the endpoint.
    if not settings.reload_feed_token:
        raise HTTPException(status_code=503, detail="feed reload is disabled")
    if x_admin_token != settings.reload_feed_token:
        raise HTTPException(status_code=403, detail="invalid reload token")
    try:
        with _feed_reload_lock:
            count, source = orchestrator.reload_products(refresh=True)
    except Exception as exc:
        trace_id = uuid4().hex
        logger.error(
            "Feed reload failed trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "FEED_RELOAD_FAILED",
                "message": "Не удалось обновить каталог.",
                "trace_id": trace_id,
            },
        ) from exc
    return {"status": "ok", "products_count": count, "source": source}
