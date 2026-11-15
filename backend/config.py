"""Settings, read from environment variables (or a .env file in the repo root)."""
import os

from dotenv import load_dotenv

from .chain import ROOT

load_dotenv(ROOT / ".env")

RPC_URL = os.getenv("RPC_URL", "https://testnet-rpc.monad.xyz")
# Sent to browsers for "add network". Keep it a public endpoint: never put an API key here.
PUBLIC_RPC_URL = os.getenv("PUBLIC_RPC_URL", "https://testnet-rpc.monad.xyz")
CHAIN_ID = int(os.getenv("CHAIN_ID", "10143"))
CHAIN_NAME = os.getenv("CHAIN_NAME", "Monad Testnet")
EXPLORER_URL = os.getenv("EXPLORER_URL", "https://testnet.monadexplorer.com")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "")
START_BLOCK = int(os.getenv("START_BLOCK", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'rps.db'}")

# Indexer tuning. Public RPCs cap eth_getLogs ranges, so we page in small chunks.
LOG_CHUNK = int(os.getenv("LOG_CHUNK", "100"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1.0"))
CONFIRMATIONS = int(os.getenv("CONFIRMATIONS", "2"))

ELO_START = 1200
ELO_K = 32
