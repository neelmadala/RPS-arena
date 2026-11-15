# CLAUDE.md: RPS Arena on Monad

Handoff notes for Claude Code. Read this before making changes.

## What this is

Commit-reveal rock-paper-scissors with stakes, on the Monad blockchain (EVM-compatible, fast
blocks, cheap transactions). Players stake MON. The contract holds the stakes and pays the
winner. A Python backend indexes contract events into a database, keeps an Elo leaderboard,
and pushes live updates to a single-page frontend.

Status: working starter. 17 tests pass on an in-memory chain. The UI was checked in headless
Chrome against a local chain. It has **not yet been deployed to Monad testnet** by the
original author. The owner is setting that up now.

## Stack

- **Contract:** Solidity 0.8.24, `contracts/RPSArena.sol`, compiled with solcjs (npm `solc`), no Foundry/Hardhat
- **Backend:** Python 3.12, web3.py 7, FastAPI, SQLAlchemy 2 (SQLite default), uvicorn
- **Frontend:** one file, `frontend/index.html`, plain JS + ethers v6 from the jsDelivr CDN, no build step
- **Tests:** pytest + `web3[tester]` (eth-tester/py-evm in-memory chain), so no RPC or node is needed
- **Network:** Monad testnet, chain ID `10143`, RPC `https://testnet-rpc.monad.xyz` (mainnet is `143`)

## Commands

The owner uses a conda environment named `rps-arena`. Activate it first: `conda activate rps-arena`.

```bash
pip install -r requirements.txt
pytest -q                          # run after every change; all tests must pass
uvicorn backend.api:app --reload   # API + frontend at http://localhost:8000
python -m scripts.deploy           # deploy contract; writes CONTRACT_ADDRESS + START_BLOCK to .env
python -m scripts.bot              # bot opponent (needs BOT_PRIVATE_KEY)
python -m backend.indexer          # indexer on its own (normally runs inside the API process)
npm install && npm run compile     # recompile contract -> artifacts/RPSArena.json (needs Node)
```

## Layout

```
contracts/RPSArena.sol     game contract
artifacts/RPSArena.json    compiled ABI + bytecode, COMMITTED. Python and the frontend load this
backend/chain.py           ABI loading, MOVES/STATUS maps, make_commitment(), send_tx(), get_web3()
backend/config.py          all settings from env/.env
backend/db.py              SQLAlchemy models: Match, Player (Elo), Meta (indexer cursor)
backend/indexer.py         eth_getLogs in chunks -> DB; Elo updates; Indexer.run() async loop
backend/api.py             create_app() factory: REST, /ws WebSocket hub, serves frontend/ at /
scripts/deploy.py          deploy + upsert .env
scripts/bot.py             host/join bot; secrets saved in bot_secrets.json
scripts/compile.js         solcjs compile script
tests/test_contract.py     contract behavior (outcomes, payouts, reveals, timeouts, cancel)
tests/test_backend.py      indexer + API + bot + live WebSocket, all on eth-tester
```

## Game protocol (keep these invariants)

1. `createMatch(commitment)` + stake as msg.value. Only the host commits.
   `commitment = keccak256(abi.encodePacked(address(this), creator, uint8(move), bytes32 salt))`
2. `joinMatch(id, move)` with an equal stake. The joiner's move is **public on purpose**: the host is
   already locked in, and a host who won't reveal forfeits.
3. `reveal(id, move, salt)` by the host before `revealDeadline` settles and pays. Draws refund both players.
4. `claimTimeout(id)` lets the joiner take the pot once the deadline has passed.
5. `cancelMatch(id)` by the host while the match is still Open refunds the stake.

- Moves: `1=rock, 2=paper, 3=scissors` (0 = None). Status: `0 None, 1 Open, 2 Joined, 3 Settled, 4 Cancelled`.
- `getMatch(id)` tuple order (the bot reads it by index): `creator, joiner, stake, commitment,
  creatorMove, joinerMove, status, revealDeadline`. **If you change the struct, update `scripts/bot.py`.**
- Payouts use push with a 30k gas cap, falling back to `credits` + `withdraw()` so a reverting receiver can't block settlement.
- `nonReentrant` on every function that moves funds. The reveal window is set in the constructor (default 600s).
- The Python `make_commitment` must match the contract's `makeCommitment`. A test enforces this.

## Gotchas

- **Changed the contract?** Run `npm run compile`, commit `artifacts/RPSArena.json`, then rerun pytest.
  Struct, event or argument changes also mean updating `backend/indexer.py` (`_apply`), `scripts/bot.py`
  (tuple indexes), and `frontend/index.html`. Changes to a deployed contract need a redeploy.
- **eth-tester raises `eth_tester.exceptions.TransactionFailed` for custom-error reverts**, not
  `ContractLogicError`. Tests use `REVERT = (ContractLogicError, TransactionFailed)`.
- **Time travel in tests:** `provider.ethereum_tester.time_travel(ts)` then `mine_blocks(1)`.
- **Indexer cursor** is stored in `Meta` under `last_block:<contract address>`, so a new deployment
  re-indexes from its own `START_BLOCK`. Matches are idempotent: replaying events is safe.
- `CONFIRMATIONS=2` by default: the indexer stays 2 blocks behind the tip. Tests mine extra blocks or pass `confirmations=0`.
- `LOG_CHUNK=100` because public RPCs cap the `eth_getLogs` block range.
- `backend/api.py` runs `app = create_app()` at import, which creates `rps.db` in the repo root. Tests use
  `create_app(w3=..., contract_address=..., session_factory=..., run_indexer=False)` with a temporary DB.
- **Frontend secrets** are in `localStorage["rps-secrets"]`, keyed by **commitment hash** (lowercase), and saved
  *before* the create transaction is sent. Reveal looks up the secret via `getMatch(id).commitment`.
- `PUBLIC_RPC_URL` is sent to browsers; `RPC_URL` is backend-only (it may contain an API key).
- Private keys work with or without `0x` (64 hex characters). The owner once pasted an address by mistake,
  which caused "must be 32 bytes, got 20".

## Rules for working here

- **Never read, print, log or commit `.env`, `bot_secrets.json`, or any private key.** Use `.env.example` for new settings.
- Keep `pytest -q` green. Add tests for new contract or backend behavior (follow the existing fixtures).
- Testnet only. Real-money betting is regulated, and the contract is unaudited.
- Keep the frontend build-free unless the owner agrees to add tooling.

## Roadmap (agreed priorities)

1. **Auto-reveal (opt-in).** The host gives the backend its secret (e.g. `POST /api/secrets` with
   commitment, move, salt, plus a wallet signature proving ownership). A backend worker reveals as soon as
   the indexer sees `MatchJoined`. Note: a backend can only call `reveal` if the contract accepts
   reveals from someone other than the creator. **Contract change:** let anyone call `reveal`
   (the commitment already binds the creator's address, so this is safe), or add an approved
   revealer. Reuse the bot's reveal logic.
2. **Salts derived from a signature.** `salt = keccak256(signMessage("RPS Arena salt v1 | contract | nonce"))`,
   so secrets can be rebuilt on any device. Keep localStorage as a cache. Track the nonce per wallet
   (e.g. a count of the player's matches).
3. **Learning bot.** Build move-history features from the DB (frequency, move after a win/loss, Markov
   order 1-2), with difficulty levels: random to adaptive. Keep the current bot as the "easy" level.
4. **Best-of-N series.** The contract tracks rounds, and the stake settles at the end. Bigger change: new events,
   indexer and UI updates.
5. Then: private challenges (`createMatch` with an allowed opponent), session keys or an embedded wallet,
   tournaments, seasons + NFT badges, ERC-20 stakes, player stats pages.

**Production hardening (before mainnet):** audit, Foundry fuzz tests, optional fee + pause + max stake,
index only finalized blocks, separate indexer process, Postgres, Redis pub/sub for the WebSocket feed,
the bot reading from the DB instead of scanning `getMatch`, Docker Compose.