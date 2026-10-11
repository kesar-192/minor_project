"""Runs one user request: planner <-> MCP server, with limits the model cannot talk its way past.

Invariants kept here, in plain code, whatever the model does:
- One trace id per user request, shared by every tool call made for it, so the dashboard groups them.
- Read-only lookups (get_balance, get_transaction_status, get_policy_status) are capped.
- Any other tool (a transfer, a key request, an unknown name) is final. After it the loop stops.
  So there is at most one transfer per request, and a DENY is never followed by a retry with
  tweaked values, which is how a split-the-amount attack would work.
- The answer shown to the user after such a call is written here from the Vault's own fields.
  The model does not get to describe a decision.
- Nothing is filtered by tool name. An unlisted or forbidden tool still goes to the Vault, so the
  Vault's log and the dashboard show the attempt.
"""

import json
import logging
from dataclasses import dataclass, field
from uuid import uuid4

from secureagent_mcp.server import TRACE_META_KEY
from secureagent_mcp.vault_client import local_denial

from .guard import render_for_llm
from .messages import PlannerError, Say, ToolResult, ToolSpec, UserMsg

log = logging.getLogger("secureagent_agent")

READ_ONLY_TOOLS = frozenset({"get_balance", "get_transaction_status", "get_policy_status"})


@dataclass
class CallRecord:
    tool: str
    arguments: dict
    reply: dict          # the Vault's answer, or the denial made locally
    is_error: bool       # True when the denial was made locally


@dataclass
class AgentResult:
    text: str
    trace_id: str
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def final(self):
        return self.calls[-1].reply if self.calls else None


def describe_decision(tool, body):
    """What the user is told after a call that ends the request. Built only from the Vault's fields."""
    if body.get("source") == "mcp_server":
        return f"No decision could be obtained from the Vault ({body.get('reason_code')}). {body.get('reason')} Nothing was confirmed."
    if body.get("decision") == "DENY":
        where = ""
        if body.get("gate_number") is not None:
            where = f" at gate {body['gate_number']} ({body.get('gate')})"
        return f"Blocked by the Vault{where}: {body.get('reason')} [{body.get('reason_code')}]"
    result = body.get("result") or {}
    if tool == "propose_transfer":
        status, tx_hash = result.get("status"), result.get("tx_hash")
        if status == "submitted":
            return f"Approved. The Vault signed the transfer and sent it to the chain. Transaction: {tx_hash}"
        if status == "submit_failed":
            return f"Approved and signed by the Vault, but handing it to the blockchain failed. Transaction: {tx_hash}"
        if status == "stub":
            return "Approved by the Vault in stub mode: nothing was signed or sent."
    return f"The Vault allowed {tool}."


class AgentSession:
    def __init__(self, planner, mcp, max_steps=4, max_lookups=2):
        self.planner = planner
        self.mcp = mcp                 # a connected mcp.Client
        self.max_steps = max_steps
        self.max_lookups = max_lookups
        self._tools = None

    async def tools(self):
        if self._tools is None:
            listing = await self.mcp.list_tools()
            self._tools = [ToolSpec(t.name, t.description or "", t.input_schema) for t in listing.tools]
        return self._tools

    async def handle(self, user_text):
        trace_id = str(uuid4())
        result = AgentResult(text="", trace_id=trace_id)
        history = [UserMsg(user_text)]
        lookups = 0
        try:
            tools = await self.tools()
        except Exception as exc:
            result.text = f"The tool server is not available ({type(exc).__name__}), so nothing was done."
            return result

        for _ in range(self.max_steps):
            try:
                step = await self.planner.next_step(history, tools)
            except PlannerError as exc:
                result.text = f"I could not work out what to do: {exc}"
                return result
            if isinstance(step, Say):
                result.text = step.text
                return result

            read_only = step.name in READ_ONLY_TOOLS
            if read_only:
                if lookups >= self.max_lookups:
                    result.text = "I stopped: that needed more lookups than one request is allowed."
                    return result
                lookups += 1

            reply, is_error = await self._call(step, trace_id)
            result.calls.append(CallRecord(step.name, step.arguments, reply, is_error))
            if not read_only:
                result.text = describe_decision(step.name, reply)
                return result
            history += [step, ToolResult(step.id, render_for_llm(step.name, reply), is_error)]

        result.text = "I stopped: that took more steps than one request is allowed."
        return result

    async def _call(self, step, trace_id):
        try:
            outcome = await self.mcp.call_tool(step.name, step.arguments, meta={TRACE_META_KEY: trace_id})
        except Exception as exc:
            log.warning("trace=%s: MCP call failed (%s)", trace_id, type(exc).__name__)
            return local_denial(trace_id, "MCP_UNAVAILABLE",
                                "The tool server did not answer.", outcome_unknown=True), True
        body = outcome.structured_content
        if not isinstance(body, dict):
            try:
                body = json.loads(outcome.content[0].text)
            except (ValueError, IndexError, AttributeError, TypeError):
                body = None
        if not isinstance(body, dict) or body.get("decision") not in ("ALLOW", "DENY"):
            return local_denial(trace_id, "MCP_BAD_RESPONSE", "The tool server's answer could not be read."), True
        return body, bool(outcome.is_error)
