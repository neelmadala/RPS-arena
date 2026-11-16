# ✊✋✌️ RPS Arena

**Rock-paper-scissors for real stakes, played entirely on-chain on Monad, with no wallet popup on every move.**

*Monad Blitz Oregon submission*

![RPS Arena screenshot](docs-screenshot.png)

| | |
|---|---|
| **Live demo** | https://stuffed-journalist-villages-super.trycloudflare.com *(runs from the team's laptop, so it is only up while we are; see [Run it yourself](#run-it-yourself) to run your own copy)* |
| **Contract** | [`0x5B6f856133A724f719b8bd764858CDa12F8C196F`](https://testnet.monadexplorer.com/address/0x5B6f856133A724f719b8bd764858CDa12F8C196F) on Monad Testnet (chain ID `10143`) |
| **Team** | Tyler Sellin ([@tsellin62](https://github.com/tsellin62)) · Neel Madala ([@neelmadala](https://github.com/neelmadala)) |
| **Built with** | Solidity 0.8.24 · Python (FastAPI, web3.py, SQLAlchemy) · ethers.js v6 · no build step |

## The idea

Most on-chain games put the money on-chain but run the game itself on a server you have to trust.
In RPS Arena the **contract is the referee**: it holds both stakes, hides the host's move until
it's revealed, decides the winner and pays out in the same transaction. The backend only
*displays* what happened on-chain. It can't change a result or touch funds.

The catch with on-chain games is the UX: every move means another wallet popup. Monad's fast
blocks and cheap gas make it realistic to fix that with **session keys**. You approve once, and
then you can play as many rounds as you like without another popup.

## Features

- **Hidden moves with commit-reveal:** the host locks in `keccak256(contract, host, move, salt)`
  so nobody can see their move. The commitment includes both addresses, so a hash can't be copied.
- **Stakes held by the contract:** the winner takes the pot right away, and draws refund both players.
- **No stalling:** if the host doesn't reveal within 10 minutes, the challenger claims the pot.
- **⚡ Session keys: play without popups.** One approval opens a session. After that:
  - a temporary key in your browser creates, joins and reveals matches for you;
  - stakes come from an in-contract game balance, and winnings go back into it;
  - the contract records **your wallet** as the player, not the key, so your stats stay yours.
- **Auto-reveal:** with a session active, your move is revealed the moment someone joins your
  match. No forgotten reveals, no forfeits.
- **Live Elo leaderboard:** a Python indexer reads contract events into a database, and the page
  updates live over a WebSocket.
- **Bot opponent:** runs from its own wallet, so there's always someone to play against.

## How a match works

1. **Create:** the host picks a move and a stake. Only the hash of the move goes on-chain.
2. **Join:** a challenger matches the stake and plays their move openly.
3. **Reveal:** the host sends the move and salt. The contract checks them against the hash, picks
   the winner and pays out.
4. **Timeout:** if the host never reveals, the challenger takes the pot with `claimTimeout`.

**Why the challenger's move can be public:** the host is already locked in by the hash, and a
host who sees they lost and refuses to reveal just forfeits. That makes it 3 transactions
instead of the 4 needed when both players commit.

## How session keys work

```
Your wallet ──(1 approval)──► startSession(key, duration, allowance, gasFund) + deposit
                                     │
 Browser key ──(no popups)──► createMatchFromBalance / joinMatchFromBalance / reveal
                                     │   recorded as YOUR wallet, paid from YOUR game balance
 Your wallet ◄──────────────── withdraw()   (only your wallet can, never the key)
```

The key's powers are deliberately narrow:

- It can stake **only from the game balance**, up to its allowance, and only until it expires.
- It can **never withdraw** and can't open new sessions.
- After it expires it can still *settle* your open matches (reveal, claim, cancel), since that
  only ever pays you.
- Ending the session revokes the key on-chain, and the page sends the key's leftover gas back to
  your wallet.

All the original functions are unchanged, so normal wallet play (and the bot) still works.

## Architecture

```
Browser (wallet or session key signs) ─────► Monad: RPSArena.sol
   ▲  WebSocket + REST                              │ events
   │                                                ▼
FastAPI  ◄── SQLite/Postgres ◄── Python indexer (web3.py, eth_getLogs)
                                   ▲
Bot opponent (its own wallet) ─────┘ plays via the contract
```

| Path | What it is |
|---|---|
| `contracts/RPSArena.sol` | The game contract, including session keys |
| `artifacts/RPSArena.json` | Compiled ABI and bytecode (already built) |
| `backend/indexer.py` | Reads contract events into the database and computes Elo |
| `backend/api.py` | FastAPI: REST, WebSocket live feed, serves the frontend |
| `backend/chain.py` | ABI, commitment helper, transaction helper |
| `frontend/index.html` | Single-page client (ethers.js, no build step) |
| `scripts/deploy.py` | Deploys the contract and writes the address to `.env` |
| `scripts/bot.py` | Bot opponent that hosts and joins matches |
| `scripts/compile.py` / `compile.js` | Recompile the contract (Python or Node) |
| `tests/` | Contract, session-key and backend/bot tests on an in-memory chain |

## Run it yourself

You need Python 3.10+ and a testnet wallet with some MON from the faucet at https://testnet.monad.xyz.
A second wallet is optional, for the bot.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in PRIVATE_KEY (and BOT_PRIVATE_KEY for the bot)

pytest -q                   # 28 tests on an in-memory chain, no RPC needed
python -m scripts.deploy    # deploys to Monad testnet, saves CONTRACT_ADDRESS + START_BLOCK
uvicorn backend.api:app --reload
```

Open http://localhost:8000, connect your wallet (the page offers to add Monad Testnet) and play.
To play against the submitted contract instead of deploying your own, set
`CONTRACT_ADDRESS=0x5B6f856133A724f719b8bd764858CDa12F8C196F` and `START_BLOCK=80070431` in
`.env` and skip the deploy.

To always have an opponent, run the bot in a second terminal:

```bash
python -m scripts.bot          # keeps one open match and joins others (stake ≤ BOT_MAX_STAKE)
```

**Sharing with friends:** the server runs locally, so expose it with a free Cloudflare quick tunnel
(no account needed):

```bash
cloudflared tunnel --url http://localhost:8000
```

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `RPC_URL` | `https://testnet-rpc.monad.xyz` | Used by the backend. A private URL with an API key is fine here |
| `PUBLIC_RPC_URL` | same | Sent to browsers, and used by session keys. Never put an API key here |
| `CHAIN_ID` | `10143` | Monad testnet (mainnet is `143`) |
| `CONTRACT_ADDRESS`, `START_BLOCK` | — | Written by the deploy script |
| `DATABASE_URL` | `sqlite:///rps.db` | e.g. `postgresql+psycopg://user:pass@host/db` (install `psycopg`) |
| `LOG_CHUNK` | `100` | Block range per `eth_getLogs` call. Public RPCs cap this |
| `CONFIRMATIONS` | `2` | Blocks to wait before indexing, to stay safely behind the chain tip |
| `BOT_STAKE` / `BOT_MAX_STAKE` | `0.01` / `0.05` | In MON |

## Recompiling the contract

The compiled artifact is already included. After you change the Solidity:

```bash
pip install py-solc-x && python -m scripts.compile   # no Node needed
# or: npm install && npm run compile
pytest -q
```

## Things we learned building on Monad

- **A freshly funded account can briefly look empty.** Monad checks new transactions against
  state from a few blocks back, so a session key funded a moment ago can get "insufficient
  balance". The page waits and retries.
- **Gas is charged on the gas limit, and checked up front.** The worst-case fee must fit in the
  balance, so the session key is given enough gas for about 15–20 actions, and the leftover is
  returned when the session ends.

## Important notes

- **Testnet only.** Real-money betting is regulated in most places, and the contract hasn't been
  audited.
- **Move secrets and session keys live in the browser** (`localStorage`). If a host clears it
  before revealing, they forfeit. The "Back up secrets" link downloads the move secrets.
- The indexer runs inside the API process for simplicity. To scale up, run
  `python -m backend.indexer` separately and use Postgres plus a pub/sub for the WebSocket feed.

## What's next

- Salts derived from a wallet signature, so moves can be revealed from any device
- A bot that learns each opponent's habits from their move history
- Best-of-3 series, tournaments, and private matches with a specific opponent
- Seasons with a prize pool for the top of the Elo leaderboard
