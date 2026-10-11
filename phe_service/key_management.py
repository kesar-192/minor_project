import json
from pathlib import Path
from phe import paillier

KEY_FILE = Path(__file__).with_name("phe_keys.json")

def load_or_create_keys():
    """Load existing Paillier keys or generate a new keypair."""

    if KEY_FILE.exists():
        with open(KEY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        public_key = paillier.PaillierPublicKey(
            n=int(data["n"])
        )

        private_key = paillier.PaillierPrivateKey(
            public_key,
            p=int(data["p"]),
            q=int(data["q"])
        )

        return public_key, private_key

    public_key, private_key = paillier.generate_paillier_keypair()

    data = {
        "n": str(public_key.n),
        "p": str(private_key.p),
        "q": str(private_key.q)
    }

    with open(KEY_FILE, "x", encoding="utf-8") as file:
        json.dump(data, file)

    return public_key, private_key