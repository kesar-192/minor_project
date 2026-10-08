"""Runs a tool call through the format check and the six gates, deny-by-default.

The first failing gate stops the request. The tool only runs, and a transfer is
only signed, after every gate that applies has passed. Anything that goes wrong
along the way (a broken policy file, a gate that crashes) ends in DENY, never ALLOW.
"""

import json
import logging
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from .actions import Done, execute
from .chain import Chain
from .events import utc_now
from .gates import REAL_GATES, STUB_GATES, Ctx, GateResult
from .handoff import DirectSubmitter, HttpSubmitter
from .models import ERROR_CODES, GATE_LABELS, SEVERITY, Check, Decision, ToolCall, describe_error
from .policy import PolicyError, PolicyStore
from .signer import VaultSigner
from .tools import TOOL_ARGS, TRANSFER_TOOLS, run_stub

log = logging.getLogger("vault_gateway")

LAST_READ_ONLY_GATE = 2  # tools that don't move funds stop after gate 2


@dataclass
class Outcome:
    """The decision, plus what the dashboard event needs to describe the request."""
    decision: Decision
    agent_id: str | None = None
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    summary: str | None = None  # plain-English line shown for ALLOW events
    tx_hash: str | None = None
    tx_status: str | None = None

    def event_fields(self):
        d = self.decision
        denied = d.decision == "DENY"
        return {
            "type": "error" if d.reason_code in ERROR_CODES else "decision",
            "timestamp": utc_now(),
            "trace_id": d.trace_id,
            "agent_id": self.agent_id,
            "tool": self.tool,
            "args": self.args,
            "decision": d.decision,
            "gate": d.gate,
            "gate_number": d.gate_number,
            "gate_label": GATE_LABELS.get(d.gate_number),
            "reason_code": d.reason_code,
            "reason": d.reason if denied else self.summary,
            "severity": SEVERITY[d.reason_code] if denied else "info",
            "checks": [c.model_dump() for c in d.checks],
            "tx_hash": self.tx_hash,
            "tx_status": self.tx_status,
        }


class InvalidRequest(Exception):
    def __init__(self, data, problem):
        super().__init__(problem)
        self.data = data
        self.problem = problem


def parse_request(body):
    """Gate 0: the request must be well-formed before any gate looks at it."""
    try:
        data = json.loads(body)
    except ValueError:
        raise InvalidRequest({}, "request body is not valid JSON")
    if not isinstance(data, dict):
        raise InvalidRequest({}, "request body must be a JSON object")
    try:
        call = ToolCall.model_validate(data)
    except ValidationError as exc:
        raise InvalidRequest(data, describe_error(exc))
    args = None
    if call.tool in TOOL_ARGS:
        try:
            args = TOOL_ARGS[call.tool].model_validate(call.args)
        except ValidationError as exc:
            raise InvalidRequest(data, describe_error(exc, "args."))
    return call, args


class Gateway:
    """Tests pass fakes for chain, signer and submitter. Normally they are built from the settings."""

    def __init__(self, settings, store, chain=None, signer=None, submitter=None):
        self.stub_mode = settings.stub_mode
        self.store = store
        self.gates = STUB_GATES if self.stub_mode else REAL_GATES
        self._transfer_lock = threading.Lock()
        self.policies = self.chain = self.signer = self.sender = self.submitter = None
        if self.stub_mode:
            return
        # Any of these can stop start-up: a bad policy file, no Vault, or a Vault
        # token that could export the key.
        self.policies = PolicyStore(settings.policy_path)
        self.chain = chain or Chain(settings.eth_rpc_url)
        self.signer = signer or VaultSigner(settings.vault_addr, settings.vault_token, settings.signer_address)
        self.sender = self.signer.address
        if submitter:
            self.submitter = submitter
        elif settings.submitter_url:
            self.submitter = HttpSubmitter(settings.submitter_url)
        else:
            self.submitter = DirectSubmitter(self.chain)
        log.info("real mode: signing account %s, hand-off via %s", self.sender,
                 settings.submitter_url or "this gateway, straight to the chain (standalone testing)")

    def evaluate(self, body):
        """Decide one raw request body. Always returns an Outcome, never raises on bad input."""
        try:
            call, args = parse_request(body)
        except InvalidRequest as exc:
            return _invalid(exc.data, exc.problem)
        # One transfer at a time: otherwise two requests could each fit under
        # the daily limit and together go over it.
        guard = self._transfer_lock if call.tool in TRANSFER_TOOLS else nullcontext()
        with guard:
            return self._decide(call, args)

    def _decide(self, call, args):
        event_args = args.model_dump(mode="json") if args else call.args
        checks = [Check(gate="request_format", passed=True)]

        def refuse(number, name, result):
            decision = Decision(
                trace_id=str(call.trace_id),
                decision="DENY",
                gate=name,
                gate_number=number,
                reason_code=result.reason_code,
                reason=result.reason,
                checks=checks,
            )
            return _log(Outcome(decision, call.agent_id, call.tool, event_args))

        policy = None
        if self.policies:
            try:
                policy = self.policies.current()
            except PolicyError:
                result = GateResult(
                    False,
                    "the policy file has an error (see the Vault log)",
                    "POLICY_INVALID",
                    "The policy file has an error, so every request is refused until it is fixed",
                )
                checks.append(Check(gate="identity", passed=False, detail=result.detail))
                return refuse(1, "identity", result)

        ctx = Ctx(call=call, args=args, policy=policy, chain=self.chain, store=self.store, sender=self.sender)
        last_gate = 6 if call.tool in TRANSFER_TOOLS else LAST_READ_ONLY_GATE
        for number, name, gate in self.gates:
            if number > last_gate:
                break
            try:
                result = gate(ctx)
            except Exception:
                # A gate that can't decide must refuse. The cause goes to the log,
                # not back to the agent.
                log.exception("gate %s failed (trace=%s)", name, call.trace_id)
                result = GateResult(
                    False,
                    "internal error (see the Vault log)",
                    "GATE_ERROR",
                    f"{GATE_LABELS[number]} could not be checked, so the request was refused",
                )
            checks.append(Check(gate=name, passed=result.passed, detail=result.detail))
            if not result.passed:
                return refuse(number, name, result)

        try:
            if self.stub_mode:
                done = Done(run_stub(call.tool, args), "Allowed in stub mode. The gates are not enforced yet.")
            else:
                done = execute(ctx, self.signer, self.submitter)
        except Exception:
            log.exception("%s failed after all gates passed (trace=%s)", call.tool, call.trace_id)
            return refuse(None, None, GateResult(
                False, None, "EXECUTION_FAILED",
                "All gates passed, but the action could not be completed",
            ))
        decision = Decision(trace_id=str(call.trace_id), decision="ALLOW", checks=checks, result=done.result)
        return _log(Outcome(decision, call.agent_id, call.tool, event_args, done.summary, done.tx_hash, done.tx_status))


def _invalid(data, problem):
    reason = f"Request rejected: {problem}"
    decision = Decision(
        trace_id=_trace_id(data),
        decision="DENY",
        gate="request_format",
        gate_number=0,
        reason_code="INVALID_REQUEST",
        reason=reason,
        checks=[Check(gate="request_format", passed=False, detail=reason)],
    )
    # The request couldn't be read, so describe it only as far as it can be trusted.
    agent_id = data.get("agent_id")
    tool = data.get("tool")
    args = data.get("args")
    return _log(Outcome(
        decision,
        agent_id[:64] if isinstance(agent_id, str) else None,
        tool[:64] if isinstance(tool, str) else None,
        args if isinstance(args, dict) else {},
    ))


def _trace_id(data):
    # Keep the caller's trace id when it is a real UUID, so the dashboard can still group events.
    try:
        return str(UUID(str(data["trace_id"])))
    except (KeyError, ValueError):
        return str(uuid4())


def _log(outcome):
    d = outcome.decision
    log.info("%s %s %s trace=%s", d.decision, outcome.tool, d.reason_code or "", d.trace_id)
    return outcome
