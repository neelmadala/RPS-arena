"""Keep tests independent of the developer's real .env (deployed address, start block, keys).

backend.config reads env vars at import time and load_dotenv never overrides variables that are
already set, so pinning them here, before any test module imports backend, wins over .env.
"""
import os

os.environ["CONTRACT_ADDRESS"] = ""
os.environ["START_BLOCK"] = "0"
os.environ["CONFIRMATIONS"] = "2"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory; tests pass their own DB anyway
