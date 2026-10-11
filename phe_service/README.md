# PHE Service — SecureAgent



## Overview

This service implements Paillier Partially Homomorphic Encryption (PHE) for privacy-preserving digital-asset balance aggregation.



## Features

- Paillier encryption and decryption

- Homomorphic addition of encrypted values

- Persistent key generation and loading

- FastAPI endpoints protected by an API token

- Requester-ID check for decryption

- Client-side encryption and end-to-end addition testing

- Encrypted balance store for `get_balance` (open point 1, demo-seeded)



## Files

- `main.py` — FastAPI endpoints

- `phe\_logic.py` — Encryption, addition, and decryption logic

- `key\_management.py` — Key management

- `client\_encrypt.py` — Client workflow

- `phe\_test.py` — Tests

- `balance\_store.py` — Encrypted per-(requester, asset) balances

- `seed\_balance.py` — Store a demo balance (open point 1)

- `requirements.txt` — Dependencies

## Setup

Install dependencies:

`pip install -r requirements.txt`

Set the API token in PowerShell:

`$env:PHE\_API\_TOKEN = "your-local-development-token"`

Start the service from this directory on port 8300 (port 8000 is used by the Vault gateway):

`py -m uvicorn main:app --reload --port 8300`

API documentation: `http://127.0.0.1:8300/docs`

## Balance endpoints (get_balance)

The Vault's `get_balance` tool calls these (open point 1) and passes the answer back to the agent unchanged.

- `POST /balance/set` — `{"requester_id", "asset", "ciphertext", "exponent", "decimals"}` stores an encrypted balance. The client encrypts with the public key, so plaintext never reaches this service.
- `POST /balance/query` — `{"requester_id", "asset"}` decrypts for an authorized requester and returns `{"asset", "balance"}` — a plain decimal string such as `"9.98"`.

Demo seed:

`PHE\_API\_TOKEN=<token> python seed\_balance.py --balance 9.98 --asset ETH`

Balances are demo data kept in memory and are not read from the chain. The chain-fed design for the future is in `vault\_gateway/contracts/CHAIN\_FED\_BALANCE.md`.

## Test

Run the client in a second terminal:

`py client\_encrypt.py`


Tested example:

- First balance: 150

- Second balance: 250

- Expected total: 400

- Actual decrypted total: 400

- Result: Test passed


## Security Limitations

The current requester-ID check does not independently authenticate the requester's identity. Proper authentication and authorization must be integrated before production deployment.



The `phe\_keys.json` file contains sensitive private-key material and must never be committed to GitHub. The PHE service is trusted to perform the intended computation; independent cryptographic verification of computation correctness is outside the current scope.



Actual AI-agent integration and actual balance retrieval remain pending.

