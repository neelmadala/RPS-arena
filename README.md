# ✊✋✌️ RPS Arena on Monad

![screenshot](docs-screenshot.png)

Rock-paper-scissors for real stakes, played entirely on-chain on Monad. Moves are hidden with
commit-reveal, the smart contract holds the stakes and pays the winner, and a Python backend
indexes every game into a live Elo leaderboard.

```
Browser (wallet signs moves) ──────────────► Monad: RPSArena.sol
   ▲  WebSocket + REST                              │ events
   │                                                ▼
FastAPI  ◄── SQLite/Postgres ◄── Python indexer (web3.py, eth_getLogs)
                                   ▲
Bot opponent (its own wallet) ─────┘ plays via the contract
```

## How a match works

1. **Create:** the host picks a move. The browser makes a random 32-byte `salt` and sends only
   `keccak256(contract, host, move, salt)` along with the stake. Nobody can see the move.
2. **Join:** a challenger matches the stake and plays their move in the open.
3. **Reveal:** the host sends `move + salt`. The contract checks the hash, works out the winner,
   and pays out right away (draws are refunded).
4. **Timeout:** if the host doesn't reveal within the window (default 10 minutes), the
   challenger calls `claimTimeout` and takes the pot.

**Why the challenger's move can be public:** the host's move is already locked in by the hash.
A host who sees they lost and refuses to reveal just forfeits. This takes 3 transactions
instead of the 4 needed when both players commit.

Other safety details:

- The commitment includes the host's address and the contract's address, so nobody can copy
  someone else's hash.
- Payouts are pushed with a gas cap. If the receiver rejects the transfer, the money goes to
  `credits` for a later `withdraw()`, so a hostile contract can't block a game from settling.
- There's a reentrancy guard on every function that moves funds.

## Project layout

| Path | What it is |
|---|---|
| `contracts/RPSArena.sol` | The game contract |
| `artifacts/RPSArena.json` | Compiled ABI and bytecode (already built) |
| `backend/indexer.py` | Reads contract events into the database and computes Elo |
| `backend/api.py` | FastAPI: REST, WebSocket live feed, serves the frontend |
| `backend/chain.py` | ABI, commitment helper, transaction helper |
| `frontend/index.html` | Single-page client (ethers.js, no build step) |
| `scripts/deploy.py` | Deploys the contract and writes the address to `.env` |
| `scripts/bot.py` | Bot opponent that hosts and joins matches |
| `scripts/compile.js` | Recompiles the contract with solcjs |
| `tests/` | Contract tests and backend/bot integration tests (in-memory chain) |

## Setup

You need Python 3.10+, and two testnet wallets (one for you or the deployer, one for the bot)
with some testnet MON from the faucet at https://testnet.monad.xyz.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in PRIVATE_KEY and BOT_PRIVATE_KEY

pytest -q                   # 17 tests, runs on an in-memory chain, no RPC needed
python -m scripts.deploy    # deploys to Monad testnet, saves CONTRACT_ADDRESS + START_BLOCK
uvicorn backend.api:app --reload
```

Open http://localhost:8000, connect your wallet (it will offer to add Monad Testnet), and play.

To always have an opponent, run the bot in a second terminal:

```bash
python -m scripts.bot          # keeps one open match and joins others (stake ≤ BOT_MAX_STAKE)
```

To try it with two people, use two browsers or wallet accounts: one creates a match, the
other joins, then the first one reveals.

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `RPC_URL` | `https://testnet-rpc.monad.xyz` | Used by the backend. A private URL with an API key is fine here |
| `PUBLIC_RPC_URL` | same | Sent to browsers. Never put an API key here |
| `CHAIN_ID` | `10143` | Monad testnet (mainnet is `143`) |
| `CONTRACT_ADDRESS`, `START_BLOCK` | — | Written by the deploy script |
| `DATABASE_URL` | `sqlite:///rps.db` | e.g. `postgresql+psycopg://user:pass@host/db` (install `psycopg`) |
| `LOG_CHUNK` | `100` | Block range per `eth_getLogs` call. Public RPCs cap this |
| `CONFIRMATIONS` | `2` | Blocks to wait before indexing, to stay safely behind the chain tip |
| `BOT_STAKE` / `BOT_MAX_STAKE` | `0.01` / `0.05` | In MON |

## Recompiling the contract

The compiled artifact is already included. After you change the Solidity:

```bash
npm install && npm run compile   # uses solcjs 0.8.24, writes artifacts/RPSArena.json
pytest -q
```

## Important notes

- **Secrets live in the browser** (`localStorage`, keyed by commitment). If a host clears it,
  they can't reveal and will forfeit. The "Back up secrets" link downloads them. The bot
  saves its own secrets to `bot_secrets.json` before each transaction, so it survives restarts.
- **Real-money betting is regulated** in most places. Keep it on testnet, or switch to points
  with no cash value, unless you've checked the rules where you are.
- The contract hasn't been audited. Get it reviewed before using it with real funds.
- The indexer runs inside the API process for simplicity. To scale up, run
  `python -m backend.indexer` separately and use Postgres plus a pub/sub for the WebSocket
  feed.

## Ideas for next steps

- Session keys or an embedded wallet, so players don't approve every move in a popup
- Best-of-3 series, tournaments, or private matches with a specific opponent
- A Python bot that learns from each opponent's move history (pandas makes this easy)
- Seasons with a prize pool for the top of the Elo leaderboard
