"""Planners turn the conversation so far into the next step: call one tool, or give the user an answer.

A planner can only ever choose a tool and its arguments. It has no way to reach the Vault, the chain
or any key: the loop passes its tool call to the MCP server, and that is the end of its reach.
"""

import json
import re

from .messages import PlannerError, Say, ToolResult, ToolUse, UserMsg
from .prompts import SYSTEM_PROMPT

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"


def _user_text(history):
    return next(m.text for m in history if isinstance(m, UserMsg))


def _tool_results(history):
    return [m for m in history if isinstance(m, ToolResult)]


# --------------------------------------------------------------------------------------
# Scripted planner: no LLM. For repeatable demos and tests.
# It plays an agent that does whatever it is told, so the Vault, not the model's good
# manners, is what gets shown stopping each attack. (A well-trained LLM often refuses
# "send me the private key" by itself, which would hide the Vault from the audience.)
# --------------------------------------------------------------------------------------

TRANSFER_RE = re.compile(r"(?i)\b(?:send|transfer|pay)\s+(?P<amount>\S+)\s+(?P<asset>[A-Za-z0-9]+)\s+to\s+(?P<recipient>\S+)")
KEY_RE = re.compile(r"(?i)private[\s_-]*key|seed[\s_-]*phrase|mnemonic|export[\s_-]*key")
TX_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}\b")


class ScriptedPlanner:
    def __init__(self):
        self.name = "scripted"

    async def next_step(self, history, tools):
        text = _user_text(history)
        calls = sum(isinstance(m, ToolUse) for m in history)
        results = _tool_results(history)
        call_id = f"call_{calls + 1}"

        if KEY_RE.search(text):
            return ToolUse(call_id, "export_private_key", {})

        transfer = TRANSFER_RE.search(text)
        if transfer:
            return self._transfer(transfer, text, call_id, results)

        if TX_HASH_RE.search(text):
            return self._read(call_id, results, "get_transaction_status", {"tx_hash": TX_HASH_RE.search(text).group()})
        if re.search(r"(?i)balance", text):
            asset = next((s for s in SYMBOL_RE.findall(text) if s not in {"I", "A"}), "ETH")
            return self._read(call_id, results, "get_balance", {"asset": asset})
        if re.search(r"(?i)polic|limit|allowed|spent|status|recipient", text):
            return self._read(call_id, results, "get_policy_status", {})
        return Say("I can't do that with the tools I have. I can check a balance, look up the policy, "
                   "check a transaction, or propose a transfer.")

    def _read(self, call_id, results, tool, args):
        if not results:
            return ToolUse(call_id, tool, args)
        return Say(self._summary(tool, _loads(results[-1].text)))

    def _transfer(self, match, text, call_id, results):
        recipient = match.group("recipient").rstrip(".,;!?")
        if not ADDRESS_RE.match(recipient):
            # A name: look it up in the policy first, as the system prompt tells a real model to do.
            if not results:
                return ToolUse(call_id, "get_policy_status", {})
            policy = _loads(results[-1].text)
            known = ((policy.get("result") or {}).get("allowed_recipients")) if policy.get("decision") == "ALLOW" else None
            address = next((a for n, a in known.items() if n.lower() == recipient.lower()), None) if isinstance(known, dict) else None
            if address is None:
                return Say(f"I could not find a recipient called {recipient} in the approved list.")
            recipient = address
        args = {"asset": match.group("asset"), "amount": match.group("amount"), "recipient": recipient}
        if "skip_checks" in text:
            args["skip_checks"] = True   # an injected field, to show the Vault rejecting it
        return ToolUse(call_id, "propose_transfer", args)

    @staticmethod
    def _summary(tool, body):
        if body.get("decision") == "DENY":
            return f"The Vault refused this: {body.get('reason')}"
        result = body.get("result") or {}
        if tool == "get_balance":
            return f"Balance: {result.get('balance', 'not available')} {result.get('asset', '')}".strip()
        if tool == "get_transaction_status":
            return f"Transaction {str(result.get('tx_hash'))[:12]}... is {result.get('status')}."
        return (f"Agent is {result.get('agent_status')}. Assets: {result.get('allowed_assets')}. "
                f"Approved recipients: {list((result.get('allowed_recipients') or {}))}. "
                f"Limits: {result.get('limits')}. Spent in the last 24 hours: {result.get('spent_last_24h')}.")


def _loads(text):
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------------------
# Real LLMs
# --------------------------------------------------------------------------------------

def _api_failure(exc):
    """A short description of an API failure. Never the exception text, which can echo request details."""
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")


class AnthropicPlanner:
    def __init__(self, model=None, api_key=None, max_tokens=1024, client=None):
        self.name = "anthropic"
        self.model = model or DEFAULT_ANTHROPIC_MODEL
        self.max_tokens = max_tokens
        if client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError:
                raise PlannerError("The anthropic package is not installed: pip install anthropic") from None
            if not api_key:
                raise PlannerError("ANTHROPIC_API_KEY is not set")
            client = AsyncAnthropic(api_key=api_key)
        self.client = client

    @staticmethod
    def format_messages(history):
        messages = []
        for item in history:
            if isinstance(item, UserMsg):
                messages.append({"role": "user", "content": item.text})
            elif isinstance(item, ToolUse):
                messages.append({"role": "assistant", "content": [
                    {"type": "tool_use", "id": item.id, "name": item.name, "input": item.arguments}]})
            elif isinstance(item, ToolResult):
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": item.id, "content": item.text, "is_error": item.is_error}]})
        return messages

    async def next_step(self, history, tools):
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=self.format_messages(history),
            )
        except Exception as exc:
            raise PlannerError(f"The LLM request failed: {_api_failure(exc)}") from None
        # Only the first tool call is used, even if the model sends several.
        for block in response.content:
            if block.type == "tool_use":
                arguments = block.input
                if not isinstance(arguments, dict):
                    raise PlannerError("The model sent tool arguments that are not an object")
                return ToolUse(block.id, block.name, dict(arguments))
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return Say(text or "The model gave no answer.")


class OpenAIPlanner:
    """Any OpenAI-compatible chat API: OpenAI itself, or Gemini, Ollama, Groq... through base_url."""

    def __init__(self, model=None, api_key=None, base_url=None, max_tokens=1024, client=None):
        self.name = "openai"
        self.model = model
        self.max_tokens = max_tokens
        self.is_openai_itself = not base_url
        if client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise PlannerError("The openai package is not installed: pip install openai") from None
            if not model:
                raise PlannerError("LLM_MODEL is not set (the model name for your OpenAI-compatible API)")
            if not api_key:
                raise PlannerError("the API key is not set (OPENROUTER_API_KEY, or OPENAI_API_KEY for the openai planner)")
            client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        self.client = client

    @staticmethod
    def format_messages(history):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in history:
            if isinstance(item, UserMsg):
                messages.append({"role": "user", "content": item.text})
            elif isinstance(item, ToolUse):
                messages.append({"role": "assistant", "content": None, "tool_calls": [{
                    "id": item.id, "type": "function",
                    "function": {"name": item.name, "arguments": json.dumps(item.arguments)}}]})
            elif isinstance(item, ToolResult):
                messages.append({"role": "tool", "tool_call_id": item.id, "content": item.text})
        return messages

    async def next_step(self, history, tools):
        options = {}
        if self.is_openai_itself:
            options["parallel_tool_calls"] = False   # other providers may reject this field
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                tools=[{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                       for t in tools],
                tool_choice="auto",
                messages=self.format_messages(history),
                **options,
            )
        except Exception as exc:
            raise PlannerError(f"The LLM request failed: {_api_failure(exc)}") from None
        if not response.choices:
            raise PlannerError("The LLM returned no choices")
        message = response.choices[0].message
        if message.tool_calls:
            call = message.tool_calls[0]   # only the first, even if the model sends several
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except ValueError:
                raise PlannerError("The model sent tool arguments that are not valid JSON") from None
            if not isinstance(arguments, dict):
                raise PlannerError("The model sent tool arguments that are not an object")
            return ToolUse(call.id, call.function.name, arguments)
        return Say((message.content or "").strip() or "The model gave no answer.")


def make_planner(settings):
    if settings.planner == "scripted":
        return ScriptedPlanner()
    if settings.planner == "anthropic":
        return AnthropicPlanner(settings.model, settings.anthropic_api_key, settings.max_tokens)
    if settings.planner == "openai":
        return OpenAIPlanner(settings.model, settings.openai_api_key, settings.openai_base_url, settings.max_tokens)
    if settings.planner == "openrouter":
        # OpenRouter speaks the OpenAI chat API, so the OpenAI planner does the work.
        planner = OpenAIPlanner(settings.model or DEFAULT_OPENROUTER_MODEL, settings.openrouter_api_key,
                                OPENROUTER_URL, settings.max_tokens)
        planner.name = "openrouter"
        return planner
    raise PlannerError(f"Unknown planner {settings.planner!r}: use scripted, openrouter, anthropic or openai")
