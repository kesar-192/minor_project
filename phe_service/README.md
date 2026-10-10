\# PHE Service — SecureAgent



\## Overview

This service implements Paillier Partially Homomorphic Encryption (PHE) for privacy-preserving digital-asset balance aggregation.



\## Features

\- Paillier encryption and decryption

\- Homomorphic addition of encrypted values

\- Persistent key generation and loading

\- FastAPI endpoints protected by an API token

\- Requester-ID check for decryption

\- Client-side encryption and end-to-end addition testing



\## Files

\- `main.py` — FastAPI endpoints

\- `phe\_logic.py` — Encryption, addition, and decryption logic

\- `key\_management.py` — Key management

\- `client\_encrypt.py` — Client workflow

\- `phe\_test.py` — Tests

\- `requirements.txt` — Dependencies



\## Setup

Install dependencies:



`pip install -r requirements.txt`



Set the API token in PowerShell:



`$env:PHE\_API\_TOKEN = "your-local-development-token"`



Start the service from this directory:



`py -m uvicorn main:app --reload`



API documentation: `http://127.0.0.1:8000/docs`



\## Test

Run the client in a second terminal:



`py client\_encrypt.py`



Tested example:

\- First balance: 150

\- Second balance: 250

\- Expected total: 400

\- Actual decrypted total: 400

\- Result: Test passed



\## Security Limitations

The current requester-ID check does not independently authenticate the requester's identity. Proper authentication and authorization must be integrated before production deployment.



The `phe\_keys.json` file contains sensitive private-key material and must never be committed to GitHub. The PHE service is trusted to perform the intended computation; independent cryptographic verification of computation correctness is outside the current scope.



Actual AI-agent integration and actual balance retrieval remain pending.

