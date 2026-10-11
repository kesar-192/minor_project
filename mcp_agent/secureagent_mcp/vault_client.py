"""Talks to the Vault gateway (CONTRACTS.md, sections 1 and 2).

Rules this file keeps:
- It forwards the call as it was made. It never edits, fixes, approves or denies arguments.
  Checking the request is gate 0 of the Vault, so even a malformed call must reach it and
  show up on the dashboard.
- It never retries. A transfer that was signed but whose answer got lost would be sent twice.
- It fails closed. If the Vault cannot be reached, answers with anything but HTTP 200, or answers
  with something that is not a decision for this request, the caller gets a DENY.
  That DENY is marked "source": "mcp_server" so nobody mistakes it for a Vault decision.
- The session token goes into the request body and nowhere else: not in the URL, not in a log,
  not in an error message.
"""

import json
import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx2

log = logging.getLogger("secureagent_mcp")

# Failures where the request provably never reached the Vault.
NOT_DELIVERED = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)


@dataclass(frozen=True)
class VaultReply:
    body: dict           # the Vault's JSON, untouched, or the DENY made here when it could not answer
    synthesized: bool    # True when the DENY was made here


def new_trace_id():
    return str(uuid4())


def clean_trace_id(value):
    """The caller's trace id if it is a real UUID v4, otherwise None."""
    try:
        parsed = UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None
    return str(parsed) if parsed.version == 4 else None


def _same_uuid(a, b):
    try:
        return UUID(str(a)) == UUID(str(b))
    except (ValueError, AttributeError, TypeError):
        return False


def local_denial(trace_id, reason_code, reason, **extra):
    """A DENY shaped like the Vault's, for when the Vault did not give a usable answer."""
    return {
        "trace_id": trace_id,
        "decision": "DENY",
        "gate": None,
        "gate_number": None,
        "reason_code": reason_code,
        "reason": reason,
        "checks": [],
        "result": None,
        "source": "mcp_server",
        **extra,
    }


def _bad_shape(body, trace_id):
    """Why a 200 answer is not a usable decision for this request, or None if it is fine."""
    if not isinstance(body, dict):
        return "the answer is not a JSON object"
    if body.get("decision") not in ("ALLOW", "DENY"):
        return "the answer has no valid decision"
    if not _same_uuid(body.get("trace_id"), trace_id):
        return "the answer is for a different request"
    if not isinstance(body.get("checks"), list):
        return "the answer has no list of checks"
    return None


class VaultClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self._transport = transport  # tests pass httpx2.MockTransport here
        self._http = None

    def _client(self):
        if self._http is None:
            self._http = httpx2.AsyncClient(
                timeout=httpx2.Timeout(self.settings.timeout_seconds),
                transport=self._transport,
                follow_redirects=False,
            )
        return self._http

    async def aclose(self):
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def call_tool(self, tool, args, trace_id=None):
        """Send one tool call to the Vault and return its decision. Never raises, never retries."""
        trace_id = clean_trace_id(trace_id) or new_trace_id()
        request = {
            "trace_id": trace_id,
            "agent_id": self.settings.agent_id,
            "session_token": self.settings.agent_token,
            "tool": tool,
            "args": {} if args is None else args,
        }
        url = f"{self.settings.vault_url}/v1/tool-call"
        try:
            content = json.dumps(request).encode()
        except (TypeError, ValueError):
            # Arguments that cannot be written as JSON: nothing was sent.
            log.warning("trace=%s tool=%s: arguments cannot be sent as JSON", trace_id, tool)
            return self._deny(trace_id, "REQUEST_NOT_SENT",
                              "The request could not be written as JSON, so nothing was sent. The request was refused.")
        try:
            response = await self._client().post(
                url, content=content, headers={"content-type": "application/json"},
            )
        except NOT_DELIVERED as exc:
            log.warning("trace=%s tool=%s: Vault not reachable (%s)", trace_id, tool, type(exc).__name__)
            return self._deny(trace_id, "VAULT_UNREACHABLE",
                              "The Vault could not be reached, so nothing was sent. The request was refused.")
        except httpx2.RequestError as exc:
            # Sent, but no usable answer: the Vault may or may not have acted on it.
            log.warning("trace=%s tool=%s: no answer from the Vault (%s)", trace_id, tool, type(exc).__name__)
            return self._deny(
                trace_id, "VAULT_NO_ANSWER",
                "The Vault did not answer. Nothing was confirmed, so treat the request as refused. "
                "Do not retry blindly: check the dashboard or get_policy_status before trying again.",
                outcome_unknown=True,
            )

        if response.status_code != 200:
            log.warning("trace=%s tool=%s: Vault answered HTTP %s", trace_id, tool, response.status_code)
            return self._deny(
                trace_id, "VAULT_ERROR",
                f"The Vault answered with HTTP {response.status_code}, which means it has a problem. "
                "The request was refused.",
                http_status=response.status_code,
                outcome_unknown=response.status_code >= 500,
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        problem = _bad_shape(body, trace_id)
        if problem:
            log.warning("trace=%s tool=%s: unusable Vault answer (%s)", trace_id, tool, problem)
            return self._deny(trace_id, "VAULT_BAD_RESPONSE",
                              f"The Vault's answer could not be used ({problem}). The request was refused.",
                              outcome_unknown=True)

        log.info("trace=%s tool=%s decision=%s %s", trace_id, tool, body["decision"], body.get("reason_code") or "")
        return VaultReply(body=body, synthesized=False)

    @staticmethod
    def _deny(trace_id, code, reason, **extra):
        return VaultReply(body=local_denial(trace_id, code, reason, **extra), synthesized=True)
