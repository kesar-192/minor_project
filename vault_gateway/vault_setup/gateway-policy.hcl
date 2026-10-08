# Vault ACL policy for the gateway's token.
# The gateway may find its signing account and ask Vault to sign.
# It may never read the private key out, create new keys, or delete them.

# List signing accounts (returns addresses only).
path "ethereum/accounts" {
  capabilities = ["list"]
}

# Read one account (returns its address only, never the key).
path "ethereum/accounts/+" {
  capabilities = ["read"]
}

# Sign a transaction with an account's key. The key stays inside Vault.
path "ethereum/accounts/+/sign" {
  capabilities = ["create", "update"]
}

# The plugin's export endpoint returns the raw private key. Always refuse it.
path "ethereum/export/*" {
  capabilities = ["deny"]
}
