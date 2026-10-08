"""Settings, read from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"  # Vite and create-react-app
DEFAULT_POLICY_PATH = BASE_DIR / "policy" / "policy.yaml"
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "vault.db")
DEFAULT_ETH_RPC_URL = "http://127.0.0.1:8545"  # Anvil
DEFAULT_VAULT_ADDR = "http://127.0.0.1:8200"


@dataclass(frozen=True)
class Settings:
    stub_mode: bool
    cors_origins: list[str]
    policy_path: Path = DEFAULT_POLICY_PATH
    db_path: str = DEFAULT_DB_PATH  # or ":memory:"
    eth_rpc_url: str = DEFAULT_ETH_RPC_URL
    vault_addr: str = DEFAULT_VAULT_ADDR
    vault_token: str = field(default="", repr=False)  # never printed
    signer_address: str | None = None  # only needed if Vault holds more than one account
    submitter_url: str | None = None   # Member 4's submitter; unset = send straight to the chain


def load_settings():
    origins = os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return Settings(
        stub_mode=os.environ.get("STUB_MODE") == "1",
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
        policy_path=Path(os.environ.get("POLICY_PATH", DEFAULT_POLICY_PATH)),
        db_path=os.environ.get("DB_PATH", DEFAULT_DB_PATH),
        eth_rpc_url=os.environ.get("ETH_RPC_URL", DEFAULT_ETH_RPC_URL),
        vault_addr=os.environ.get("VAULT_ADDR", DEFAULT_VAULT_ADDR),
        vault_token=os.environ.get("VAULT_TOKEN", ""),
        signer_address=os.environ.get("SIGNER_ADDRESS") or None,
        submitter_url=os.environ.get("SUBMITTER_URL") or None,
    )
