from phe import EncryptedNumber
from key_management import load_or_create_keys

public_key, private_key = load_or_create_keys()

AUTHORIZED_REQUESTERS = {"user123"}

def encrypt(value):
    """Encrypt a single numerical value using the public key."""
    return public_key.encrypt(value)

def add(ciphertext_list):
    """Add encrypted values without decrypting them."""
    if not ciphertext_list:
        raise ValueError("At least one ciphertext is required")

    total = ciphertext_list[0]

    for ciphertext in ciphertext_list[1:]:
        total = total + ciphertext

    return total

def decrypt(ciphertext, requester_id):
    """Decrypt only for an authorized requester."""
    if requester_id not in AUTHORIZED_REQUESTERS:
        raise PermissionError("Requester is not authorized to decrypt")

    return private_key.decrypt(ciphertext)

def ciphertext_to_dict(encrypted_value):
    """Convert an encrypted value to a JSON-compatible dictionary."""
    return {
        "ciphertext": str(encrypted_value.ciphertext()),
        "exponent": encrypted_value.exponent
    }

def dict_to_ciphertext(data):
    """Reconstruct an encrypted value from its serialized representation."""
    ciphertext_value = int(data["ciphertext"])
    exponent = int(data["exponent"])

    return EncryptedNumber(
        public_key,
        ciphertext_value,
        exponent
    )

def get_public_key():
    """Return public key information without exposing the private key."""
    return {
        "n": str(public_key.n)
    }
