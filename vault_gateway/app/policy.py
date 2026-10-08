"""The Vault's policy: who each agent is, what it may do, and its limits.

Loaded from policy/policy.yaml and re-read whenever the file changes, so editing
an agent's status takes effect on the very next request (the kill switch).
If the file becomes invalid, every request is refused until it is fixed.
"""

import hashlib
import hmac
import logging
import re
import threading
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from .models import Address, Amount, Asset, Strict, describe_error
from .tools import RESTRICTED_TOOLS, TOOL_ARGS

log = logging.getLogger("vault_gateway")


class PolicyError(Exception):
    pass


class Limits(Strict):
    max_per_tx: Amount
    daily: Amount

    @model_validator(mode="after")
    def per_tx_within_daily(self):
        if self.max_per_tx > self.daily:
            raise ValueError("max_per_tx cannot be larger than daily")
        return self


class AgentPolicy(Strict):
    status: Literal["active", "suspended"]
    token_sha256: str
    allowed_tools: list[str]
    allowed_assets: list[Asset]
    allowed_recipients: dict[str, Address]  # name -> address
    limits: dict[Asset, Limits]

    @field_validator("token_sha256")
    @classmethod
    def looks_like_sha256(cls, value):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("must be the SHA-256 of the agent's token; make one with scripts/new_agent_token.py")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def tools_exist(cls, tools):
        for tool in tools:
            if tool in RESTRICTED_TOOLS:
                raise ValueError(f"{tool} can never be allowed")
            if tool not in TOOL_ARGS:
                raise ValueError(f"{tool} is not in the tool catalog")
        return tools

    @model_validator(mode="after")
    def every_asset_has_limits(self):
        missing = [asset for asset in self.allowed_assets if asset not in self.limits]
        if missing:
            raise ValueError(f"no limits set for {', '.join(missing)}")
        return self

    def token_matches(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        return hmac.compare_digest(digest, self.token_sha256)  # constant time, leaks nothing via timing

    def recipient_name(self, address):
        for name, allowed in self.allowed_recipients.items():
            if allowed == address:
                return name
        return None


class Restrictions(Strict):
    private_key_export: Literal[False]  # there is no setting that turns this on


class Policy(Strict):
    agents: dict[str, AgentPolicy] = Field(min_length=1)
    restrictions: Restrictions


def load_policy(path):
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"cannot read {path}: {exc}") from exc
    try:
        return Policy.model_validate(data)
    except ValidationError as exc:
        raise PolicyError(f"{path}: {describe_error(exc)}") from exc


class PolicyStore:
    """Hands out the current policy, re-reading the file whenever it changes."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._stamp = None
        self._policy = None
        self._error = None
        self.current()  # a bad policy file stops the service from starting

    def current(self):
        with self._lock:
            try:
                stat = self.path.stat()
            except OSError as exc:
                raise PolicyError(f"cannot read {self.path}: {exc}") from exc
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp != self._stamp:
                self._stamp = stamp
                try:
                    self._policy, self._error = load_policy(self.path), None
                    log.info("policy loaded from %s", self.path)
                except PolicyError as exc:
                    # Fail closed: never fall back to the old policy, or a typo
                    # made while suspending an agent would leave it active.
                    self._policy, self._error = None, str(exc)
                    log.error("%s. Refusing every request until it is fixed.", exc)
            if self._error:
                raise PolicyError(self._error)
            return self._policy
