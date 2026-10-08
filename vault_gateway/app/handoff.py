"""Handing a signed transaction on to be sent to the chain (CONTRACTS.md, section 4).

HttpSubmitter is the real hand-off to Member 4's submitter (set SUBMITTER_URL).
DirectSubmitter sends it to Anvil itself, for testing the Vault on its own.
Either way, a failure is reported as "submit_failed", never raised.
"""

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("vault_gateway")


class HttpSubmitter:
    def __init__(self, base_url, timeout=5):
        self.url = base_url.rstrip("/") + "/v1/submit"
        self.timeout = timeout

    def submit(self, handoff):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(handoff).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            # urlopen raises HTTPError for any 4xx/5xx answer.
            with urllib.request.urlopen(request, timeout=self.timeout):
                return "submitted"
        except (urllib.error.URLError, OSError):
            log.exception("hand-off to %s failed (trace=%s)", self.url, handoff["trace_id"])
            return "submit_failed"


class DirectSubmitter:
    """Standalone testing only. In the integrated demo, Member 4's submitter does this."""

    def __init__(self, chain):
        self.chain = chain

    def submit(self, handoff):
        try:
            self.chain.send_raw_transaction(handoff["raw_tx"])
            return "submitted"
        except Exception:
            log.exception("sending the transaction failed (trace=%s)", handoff["trace_id"])
            return "submit_failed"
