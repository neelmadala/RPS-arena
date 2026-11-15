"""Deploy RPSArena to Monad and save the address to .env.

Usage:
    python -m scripts.deploy               # uses PRIVATE_KEY and RPC_URL from .env
    python -m scripts.deploy --window 300  # reveal window in seconds (default 600)
"""
import argparse
import os
import re

from eth_account import Account

from backend import config
from backend.chain import ABI, BYTECODE, ROOT, get_web3


def upsert_env(values: dict):
    path = ROOT / ".env"
    text = path.read_text() if path.exists() else ""
    for k, v in values.items():
        line = f"{k}={v}"
        if re.search(rf"^{k}=.*$", text, flags=re.M):
            text = re.sub(rf"^{k}=.*$", line, text, flags=re.M)
        else:
            text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    path.write_text(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=600, help="reveal window in seconds")
    args = ap.parse_args()

    key = os.getenv("PRIVATE_KEY")
    if not key:
        raise SystemExit("Set PRIVATE_KEY in .env (a testnet wallet with some MON for gas).")
    w3 = get_web3(config.RPC_URL)
    acct = Account.from_key(key)
    print(f"Deploying from {acct.address} to chain {w3.eth.chain_id} via {config.RPC_URL}")
    print(f"Balance: {w3.from_wei(w3.eth.get_balance(acct.address), 'ether')} MON")

    factory = w3.eth.contract(abi=ABI, bytecode=BYTECODE)
    tx = factory.constructor(args.window).build_transaction(
        {"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "chainId": w3.eth.chain_id}
    )
    tx_hash = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    print(f"tx: {tx_hash.to_0x_hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        raise SystemExit("Deployment reverted")

    print(f"RPSArena deployed at {receipt.contractAddress} (block {receipt.blockNumber})")
    print(f"Explorer: {config.EXPLORER_URL}/address/{receipt.contractAddress}")
    upsert_env({"CONTRACT_ADDRESS": receipt.contractAddress, "START_BLOCK": receipt.blockNumber})
    print("Saved CONTRACT_ADDRESS and START_BLOCK to .env")


if __name__ == "__main__":
    main()
