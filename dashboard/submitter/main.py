"""Blockchain submitter (Member 4) - CONTRACTS.md section 4.
POST /v1/submit  -> send_raw_transaction to Anvil, answer 202 at once.
A background task then polls the receipt: pending -> confirmed | failed.
No signing code lives here; the Vault already signed the transaction."""
import asyncio, os, re, time
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
from web3 import Web3
from web3.exceptions import TransactionNotFound

RPC_URL = os.environ.get("ANVIL_RPC_URL", "http://127.0.0.1:8545")
ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
POLL_SECONDS, POLL_TIMEOUT = 1.0, 120
w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 4}))
TXS: dict[str, dict] = {}  # tx_hash(lowercase) -> record (in memory is enough for the demo)

app = FastAPI(title="SecureAgent blockchain submitter")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)  # same strictness as the Vault
    trace_id: str
    raw_tx: str
    tx_hash: str
    from_: str = Field(alias="from")  # "from" is a Python keyword
    to: str
    value_wei: str
    chain_id: int

    @field_validator("raw_tx")
    @classmethod
    def _raw(cls, v):
        if not re.fullmatch(r"0x[0-9a-fA-F]+", v):
            raise ValueError("raw_tx must be 0x-prefixed hex")
        return v


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z"


async def _track(key: str):
    rec, deadline = TXS[key], time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            r = await run_in_threadpool(w3.eth.get_transaction_receipt, key)
            rec.update(status="confirmed" if r["status"] == 1 else "failed",
                       block_number=r["blockNumber"], gas_used=r["gasUsed"], updated_at=_now())
            return
        except TransactionNotFound:
            pass
        except Exception:  # RPC hiccup: keep trying until the deadline
            pass
        await asyncio.sleep(POLL_SECONDS)
    rec.update(status="failed", error="no receipt before timeout", updated_at=_now())


@app.post("/v1/submit", status_code=202)
async def submit(h: Handoff):
    key = h.tx_hash.lower()
    if key in TXS:  # Vault retried: stay idempotent
        return {"tx_hash": h.tx_hash, "status": "submitted"}
    try:
        node_hash = (await run_in_threadpool(w3.eth.send_raw_transaction, h.raw_tx)).hex()
    except Exception as e:  # non-2xx -> the Vault reports submit_failed
        raise HTTPException(status_code=502, detail=f"node rejected transaction: {e}")
    node_hash = node_hash if node_hash.startswith("0x") else "0x" + node_hash
    if node_hash.lower() != key:  # should never happen; surface it loudly
        key = node_hash.lower()
    TXS[key] = dict(tx_hash=node_hash, trace_id=h.trace_id, status="pending", sender=h.from_, to=h.to,
                    value_wei=h.value_wei, chain_id=h.chain_id, submitted_at=_now(), updated_at=_now())
    asyncio.create_task(_track(key))
    return {"tx_hash": node_hash, "status": "submitted"}


@app.get("/v1/tx/{tx_hash}")
async def tx_status(tx_hash: str):
    rec = TXS.get(tx_hash.lower())
    return rec or {"tx_hash": tx_hash, "status": "not_found"}


@app.get("/v1/txs")
async def all_txs():
    return list(TXS.values())


@app.get("/health")
async def health():
    ok = await run_in_threadpool(w3.is_connected)
    return {"anvil_connected": ok, "rpc": RPC_URL}
