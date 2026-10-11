import requests
from phe import paillier

BASE_URL = "http://127.0.0.1:8000"
API_TOKEN = "my-local-test-token-123"

response = requests.get(
    f"{BASE_URL}/public-key",
    headers={"x-api-token": API_TOKEN}
)
response.raise_for_status()

key_data = response.json()

public_key = paillier.PaillierPublicKey(
    n=int(key_data["n"])
)

balance_a = int(input("Enter first balance: "))
balance_b = int(input("Enter second balance: "))

if balance_a < 0 or balance_b < 0:
    raise ValueError("Balances cannot be negative")


encrypted_a = public_key.encrypt(balance_a)
encrypted_b = public_key.encrypt(balance_b)

print("Encrypted A exponent:", encrypted_a.exponent)
print("Encrypted B exponent:", encrypted_b.exponent)

add_response = requests.post(
    f"{BASE_URL}/add",
    headers={"x-api-token": API_TOKEN},
    json={
        "values": [
            {
                "ciphertext": str(encrypted_a.ciphertext()),
                "exponent": encrypted_a.exponent
            },
            {
                "ciphertext": str(encrypted_b.ciphertext()),
                "exponent": encrypted_b.exponent
            }
        ]
    }
)

add_response.raise_for_status()
encrypted_total = add_response.json()

print("Encrypted addition response received successfully.")
print("Result exponent:", encrypted_total["exponent"])

decrypt_response = requests.post(
    f"{BASE_URL}/decrypt",
    headers={"x-api-token": API_TOKEN},
    json={
        "ciphertext": encrypted_total["ciphertext"],
        "exponent": encrypted_total["exponent"],
        "requester_id": "user123"
    }
)

decrypt_response.raise_for_status()

decrypted_result = decrypt_response.json()["decrypted_result"]

print("Decrypted final total:", decrypted_result)
expected_total = balance_a + balance_b
print("Expected total:", expected_total)
print("Test passed:", decrypted_result == expected_total)