"""Talks to Member 1's PHE service for the get_balance tool (open point 1).

The PHE service owns the encrypted balances and is the only source for this
tool. Any problem is raised as PheError, which the pipeline turns into a
fail-closed EXECUTION_FAILED: the Vault never invents or guesses a balance.
"""

import json
import re
import urllib.error
import urllib.request

BALANCE_RE = re.compile(r"^\d{1,30}(\.\d{1,18})?$")


class PheError(Exception):
    """The PHE service could not be reached, or did not answer with a usable balance."""


class PheClient:
    def __init__(self, base_url, api_token, requester_id, timeout=5):
        self.url = base_url.rstrip("/") + "/balance/query"
        self.api_token = api_token
        self.requester_id = requester_id
        self.timeout = timeout

    def balance(self, asset):
        payload = {"requester_id": self.requester_id, "asset": asset}
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", "x-api-token": self.api_token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise PheError(f"the PHE service answered HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PheError(
                f"the PHE service could not be reached or answered badly ({type(exc).__name__})"
            ) from exc
        return _checked(body, asset)


def _checked(body, asset):
    if not isinstance(body, dict):
        raise PheError("the PHE answer is not a JSON object")
    got_asset = body.get("asset")
    balance = body.get("balance")
    if not isinstance(got_asset, str) or got_asset.upper() != asset.upper():
        raise PheError("the PHE answer is for a different asset")
    if not isinstance(balance, str) or not BALANCE_RE.match(balance):
        raise PheError("the PHE answer is not a plain decimal balance")
    return {"asset": got_asset.upper(), "balance": balance}
