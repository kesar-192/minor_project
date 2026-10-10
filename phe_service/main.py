import os
from fastapi import FastAPI, Header, HTTPException
from phe_logic import (
    encrypt,
    add,
    decrypt,
    ciphertext_to_dict,
    dict_to_ciphertext,
    get_public_key
)
from pydantic import BaseModel, Field
from typing import List

app = FastAPI()
API_TOKEN = os.environ.get("PHE_API_TOKEN")


def verify_api_token(token: str):
    if not API_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="API token is not configured"
        )

    if not token or token != API_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Invalid API token"
        )

@app.get("/health")
def health_check():
    return {"status": "PHE service is running"}


@app.get("/public-key")
def public_key_endpoint(
    x_api_token: str = Header(default="")
):
    verify_api_token(x_api_token)
    return get_public_key()

@app.post("/encrypt")
def encrypt_number(
    value: int,
    x_api_token: str = Header(default="")
):
    verify_api_token(x_api_token)

    encrypted_value = encrypt(value)

    return {
        "message": "Number encrypted successfully",
        "encrypted_value": str(encrypted_value.ciphertext()),
        "exponent": encrypted_value.exponent
    }


class CiphertextData(BaseModel):
    ciphertext: str
    exponent: int

class DecryptRequest(BaseModel):
    ciphertext: str
    exponent: int
    requester_id: str

class AddRequest(BaseModel):
    values: List[CiphertextData] = Field(min_length=1)


@app.post("/add")
def add_numbers(
    request: AddRequest,
    x_api_token: str = Header(default="")
):
    verify_api_token(x_api_token)

    encrypted_values = [
        dict_to_ciphertext(item.model_dump())
        for item in request.values
    ]

    encrypted_total = add(encrypted_values)

    return {
        "message": "Encrypted numbers added successfully",
        **ciphertext_to_dict(encrypted_total)
    }


@app.post("/decrypt-test")
def decrypt_test(
    x_api_token: str = Header(default="")
):
    verify_api_token(x_api_token)

    encrypted_a = encrypt(100)
    encrypted_b = encrypt(200)

    encrypted_total = add([encrypted_a, encrypted_b])
    result = decrypt(encrypted_total, "user123")

    return {
        "message": "Decryption successful",
        "decrypted_total": result
    }

@app.post("/verify-add")
def verify_add(
    x_api_token: str = Header(default="")
):
    verify_api_token(x_api_token)
    # Encrypt two numbers
    encrypted_a = encrypt(100)
    encrypted_b = encrypt(200)

    # Convert ciphertexts to API format
    data_a = ciphertext_to_dict(encrypted_a)
    data_b = ciphertext_to_dict(encrypted_b)

    # Reconstruct ciphertexts as the /add endpoint does
    restored_a = dict_to_ciphertext(data_a)
    restored_b = dict_to_ciphertext(data_b)

    # Perform encrypted addition
    encrypted_total = add([restored_a, restored_b])

    # Decrypt only for this local verification
    result = decrypt(encrypted_total, "user123")

    return {
        "message": "End-to-end addition verification successful",
        "decrypted_total": result,
        "test_passed": result == 300
    }


@app.post("/decrypt")
def decrypt_result(
    request: DecryptRequest,
    x_api_token: str = Header(default="")
):
    verify_api_token(x_api_token)

    # Local development
    # The requester ID must match an authorized requester.
    if request.requester_id != "user123":
        raise HTTPException(
            status_code=403,
            detail="Requester is not authorized"
        )

    encrypted_value = dict_to_ciphertext({
        "ciphertext": request.ciphertext,
        "exponent": request.exponent
    })

    result = decrypt(
        encrypted_value,
        request.requester_id
    )

    return {
        "message": "Decryption successful",
        "decrypted_result": result
    }
