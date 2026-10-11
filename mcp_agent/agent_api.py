"""HTTP front for the agent, so the dashboard can chat with it:  uvicorn agent_api:app --port 8500

Same path as the CLI: dashboard -> this API -> agent loop -> MCP server (stdio child) -> Vault gateway.
This process never holds the Vault session token; only the MCP server child reads it from the .env file.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from mcp import Client
from pydantic import BaseModel, Field

from secureagent_agent.config import AgentSettings
from secureagent_agent.loop import AgentSession
from secureagent_agent.planners import DEFAULT_OPENROUTER_MODEL, ScriptedPlanner, make_planner
from secureagent_agent.runtime import mcp_params
from secureagent_mcp.config import PACKAGE_ROOT, load_env

log = logging.getLogger("agent_api")
ENV_FILE = PACKAGE_ROOT.parent / ".env"


class Chat(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    planner: str = "scripted"   # scripted | llm


@asynccontextmanager
async def lifespan(app):
    env = {k: v for k, v in load_env(str(ENV_FILE)).items() if k != "AGENT_TOKEN"}
    llm = AgentSettings.from_env({**env, "AGENT_PLANNER": "openrouter"})
    app.state.llm_model = llm.model or DEFAULT_OPENROUTER_MODEL
    app.state.llm_ready = bool(llm.openrouter_api_key)
    params = mcp_params(attack_demo=True, env_file=str(ENV_FILE))
    async with Client(params) as client:
        app.state.client = client
        app.state.sessions = {"scripted": AgentSession(ScriptedPlanner(), client, llm.max_steps, llm.max_lookups)}
        if app.state.llm_ready:
            app.state.sessions["llm"] = AgentSession(make_planner(llm), client, llm.max_steps, llm.max_lookups)
        yield


app = FastAPI(title="SecureAgent chat API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/info")
async def info():
    return {"llm_ready": app.state.llm_ready, "model": app.state.llm_model}


@app.get("/policy")
async def policy():
    """The agent's own policy and limits, read through the same MCP -> Vault path as any tool call."""
    outcome = await app.state.client.call_tool("get_policy_status", {})
    body = outcome.structured_content
    if not isinstance(body, dict) or body.get("decision") != "ALLOW":
        raise HTTPException(502, (body or {}).get("reason") or "the Vault did not return the policy")
    return body["result"]


@app.post("/chat")
async def chat(req: Chat):
    session = app.state.sessions.get(req.planner)
    if session is None:
        raise HTTPException(400, "No LLM is configured: set OPENROUTER_API_KEY in .env and restart ./run.sh start"
                            if req.planner == "llm" else f"unknown planner {req.planner!r}")
    result = await session.handle(req.message)
    return {
        "text": result.text,
        "trace_id": result.trace_id,
        "planner": req.planner,
        "calls": [{"tool": c.tool, "arguments": c.arguments, "decision": c.reply.get("decision"),
                   "gate_number": c.reply.get("gate_number"), "gate": c.reply.get("gate"),
                   "reason_code": c.reply.get("reason_code"), "reason": c.reply.get("reason"),
                   "local": c.is_error} for c in result.calls],
    }
