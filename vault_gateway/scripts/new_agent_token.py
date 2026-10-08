"""Make a new session token for an agent.

The token goes into the MCP server's .env. Send it to Member 2 privately and never commit it.
Its SHA-256 goes into policy/policy.yaml as the agent's token_sha256.
"""

import hashlib
import secrets

token = secrets.token_urlsafe(32)
print(f"token        (MCP server .env, keep secret): {token}")
print(f"token_sha256 (policy/policy.yaml):           {hashlib.sha256(token.encode()).hexdigest()}")
