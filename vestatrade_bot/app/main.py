from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agents.orchestrator import ChatOrchestrator
from app.chat_logger import ChatLogger
from app.config import get_settings
from app.models import ChatRequest, ChatResponse


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Vesta Trading AI Consultant", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
orchestrator = ChatOrchestrator()
chat_logger = ChatLogger(get_settings().chat_logs_dir)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup_load_feed() -> None:
    try:
        count, source = orchestrator.reload_products(refresh=True)
        logger.info("Loaded %s products from %s on startup", count, source)
    except Exception as exc:
        logger.warning("Startup feed load failed, bot will try cache on demand: %s", exc)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "products_loaded": len(orchestrator.search_agent.products),
        "products_loaded_from": orchestrator.products_loaded_from,
        "product_docs_loaded": orchestrator.docs_attached,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/styles.css")
def root_styles() -> FileResponse:
    return FileResponse(STATIC_DIR / "styles.css")


@app.get("/app.js")
def root_script() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.js")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    response = orchestrator.handle_chat(request.session_id, request.message)
    chat_logger.log_turn(request.session_id, request.message, response)
    return response


@app.post("/reload-feed")
def reload_feed() -> dict[str, Any]:
    try:
        count, source = orchestrator.reload_products(refresh=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "products_count": count, "source": source}
