"""Shared contract helpers: ABI loading, move names, commitments, web3 setup."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from web3 import Web3

ROOT = Path(__file__).resolve().parent.parent
_ARTIFACT = json.loads((ROOT / "artifacts" / "RPSArena.json").read_text())
ABI = _ARTIFACT["abi"]
BYTECODE = _ARTIFACT["bytecode"]

MOVES = {"rock": 1, "paper": 2, "scissors": 3}
MOVE_NAMES = {0: None, 1: "rock", 2: "paper", 3: "scissors"}
STATUS_NAMES = {0: "none", 1: "open", 2: "joined", 3: "settled", 4: "cancelled"}


def new_salt() -> bytes:
    return secrets.token_bytes(32)


def make_commitment(contract_address: str, player: str, move: int, salt: bytes) -> bytes:
    """Must match RPSArena.makeCommitment: keccak256(abi.encodePacked(this, player, uint8(move), salt))."""
    return Web3.solidity_keccak(
        ["address", "address", "uint8", "bytes32"],
        [Web3.to_checksum_address(contract_address), Web3.to_checksum_address(player), move, salt],
    )


def get_web3(rpc_url: str | None = None) -> Web3:
    url = rpc_url or os.getenv("RPC_URL", "https://testnet-rpc.monad.xyz")
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))


def get_contract(w3: Web3, address: str | None = None):
    addr = address or os.getenv("CONTRACT_ADDRESS")
    if not addr:
        raise RuntimeError("CONTRACT_ADDRESS is not set. Deploy first: python -m scripts.deploy")
    return w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ABI)


def send_tx(w3: Web3, account, fn, value: int = 0):
    """Build, sign and send a contract call from a LocalAccount; returns the receipt."""
    tx = fn.build_transaction(
        {
            "from": account.address,
            "value": value,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "chainId": w3.eth.chain_id,
        }
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
    return receipt
