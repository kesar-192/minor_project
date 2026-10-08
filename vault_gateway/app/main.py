"""SecureAgent Vault Gateway: the FastAPI app.

Run from the vault_gateway folder (see the README for real mode):
    STUB_MODE=1 .venv/bin/uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
"""

import logging

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .events import EventHub
from .models import Decision, ToolCall
from .pipeline import Gateway
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings=None, *, chain=None, signer=None, submitter=None):
    """Build the app. Tests pass fakes for chain, signer and submitter; normally they come from settings."""
    settings = settings or load_settings()
    app = FastAPI(title="SecureAgent Vault Gateway", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    store = Store(settings.db_path)
    hub = EventHub(store)
    gateway = Gateway(settings, store, chain=chain, signer=signer, submitter=submitter)
    app.state.gateway = gateway

    @app.get("/health")
    async def health():
        if settings.stub_mode:
            return {"status": "ok", "mode": "stub"}
        return {"status": "ok", "mode": "real", "signing_account": gateway.sender}

    # The body is read by hand (below), so describe it for the /docs page explicitly.
    request_schema = {"content": {"application/json": {"schema": ToolCall.model_json_schema()}}}

    @app.post("/v1/tool-call", response_model=Decision, openapi_extra={"requestBody": request_schema})
    async def tool_call(request: Request):
        # Read the raw body ourselves: a malformed request must still get a
        # DENY decision and a dashboard event, not FastAPI's automatic 422.
        body = await request.body()
        # Gates are plain functions that will call Vault and the chain, so run them off the event loop.
        outcome = await run_in_threadpool(gateway.evaluate, body)
        await hub.publish(outcome.event_fields())
        return outcome.decision

    @app.get("/v1/events")
    async def recent_events(limit: int = Query(50, ge=1, le=500)):
        return hub.recent(limit)

    @app.websocket("/ws/events")
    async def events_feed(ws: WebSocket):
        await hub.connect(ws)
        try:
            while True:
                await ws.receive_text()  # clients don't send anything; this just waits for them to leave
        except WebSocketDisconnect:
            hub.disconnect(ws)

    return app
