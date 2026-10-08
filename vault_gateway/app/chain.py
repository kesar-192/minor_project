"""Access to the blockchain (Anvil). The gates only read from it.

send_raw_transaction is used only by the standalone-testing submitter in
app/handoff.py. In the integrated demo, Member 4's submitter sends transactions.
"""

from web3 import Web3
from web3.exceptions import TransactionNotFound

GAS_LIMIT_ETH_TRANSFER = 21000  # a plain ETH transfer always uses exactly this much gas


class Chain:
    def __init__(self, rpc_url, timeout=5):
        # A short timeout, so a dead node fails a gate quickly instead of hanging the request.
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))

    def balance_wei(self, address):
        return self.w3.eth.get_balance(address)

    def gas_price_wei(self):
        return self.w3.eth.gas_price

    def nonce(self, address):
        # "pending" counts transactions already sent but not yet mined.
        return self.w3.eth.get_transaction_count(address, "pending")

    def chain_id(self):
        return self.w3.eth.chain_id

    def send_raw_transaction(self, raw_tx):
        return self.w3.eth.send_raw_transaction(raw_tx).to_0x_hex()

    def transaction_status(self, tx_hash):
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            try:
                self.w3.eth.get_transaction(tx_hash)
            except TransactionNotFound:
                return "not_found"
            return "pending"
        return "confirmed" if receipt["status"] == 1 else "failed"
