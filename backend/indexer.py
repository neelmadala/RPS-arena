"""Reads RPSArena events from Monad and mirrors them into the database.

Runs inside the API process (see api.py), but can also run alone:
    python -m backend.indexer
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from web3 import Web3

from . import config
from .chain import ABI, MOVE_NAMES
from .db import Match, Meta, Player

log = logging.getLogger("indexer")
ZERO = "0x0000000000000000000000000000000000000000"


def _event_map(contract) -> dict[bytes, object]:
    """topic0 -> event object, so one eth_getLogs call covers every event type."""
    out = {}
    for item in ABI:
        if item["type"] == "event":
            sig = f'{item["name"]}({",".join(i["type"] for i in item["inputs"])})'
            out[bytes(Web3.keccak(text=sig))] = getattr(contract.events, item["name"])()
    return out


def expected_score(a: float, b: float) -> float:
    return 1 / (1 + 10 ** ((b - a) / 400))


def update_elo(winner: Player, loser: Player, draw: bool = False, k: int = config.ELO_K):
    ea = expected_score(winner.elo, loser.elo)
    sa = 0.5 if draw else 1.0
    winner.elo += k * (sa - ea)
    loser.elo += k * ((1 - sa) - (1 - ea))


class Indexer:
    def __init__(
        self,
        w3: Web3,
        contract,
        session_factory,
        start_block: int = config.START_BLOCK,
        chunk: int = config.LOG_CHUNK,
        confirmations: int = config.CONFIRMATIONS,
    ):
        self.w3 = w3
        self.contract = contract
        self.Session = session_factory
        self.start_block = start_block
        self.chunk = chunk
        self.confirmations = confirmations
        self.events = _event_map(contract)
        self.meta_key = f"last_block:{contract.address.lower()}"

    # ---- sync ---------------------------------------------------------------
    def _last_block(self, s) -> int:
        row = s.get(Meta, self.meta_key)
        return int(row.value) if row else self.start_block - 1

    def sync_once(self) -> list[dict]:
        """Index everything up to (head - confirmations). Returns changed matches."""
        head = self.w3.eth.block_number - self.confirmations
        with self.Session() as s:
            last = self._last_block(s)
        changed: dict[int, dict] = {}
        while last < head:
            to = min(last + self.chunk, head)
            logs = self.w3.eth.get_logs(
                {"address": self.contract.address, "fromBlock": last + 1, "toBlock": to}
            )
            with self.Session.begin() as s:
                for raw in logs:
                    m = self._apply(s, raw)
                    if m is not None:
                        changed[m.id] = m.to_dict()
                row = s.get(Meta, self.meta_key)
                if row:
                    row.value = str(to)
                else:
                    s.add(Meta(key=self.meta_key, value=str(to)))
            last = to
        return list(changed.values())

    async def run(self, on_change: Callable[[dict], Awaitable[None]] | None = None):
        log.info("Indexing %s from block %s", self.contract.address, self.start_block)
        while True:
            try:
                for m in await asyncio.to_thread(self.sync_once):
                    if on_change:
                        await on_change(m)
            except Exception as e:  # keep going through RPC hiccups
                log.warning("indexer error: %s", e)
            await asyncio.sleep(config.POLL_INTERVAL)

    # ---- event handlers -----------------------------------------------------
    def _player(self, s, address: str) -> Player:
        p = s.get(Player, address)
        if p is None:
            p = Player(address=address, elo=config.ELO_START, wins=0, losses=0, draws=0)
            s.add(p)
        return p

    def _apply(self, s, raw) -> Match | None:
        event = self.events.get(bytes(raw["topics"][0]))
        if event is None:
            return None
        ev = event.process_log(raw)
        name, a = ev["event"], ev["args"]
        if "id" not in a:  # balance / session events don't change a match
            return None

        if name == "MatchCreated":
            m = s.get(Match, a["id"])
            if m is None:
                m = Match(
                    id=a["id"],
                    creator=a["creator"],
                    stake_wei=str(a["stake"]),
                    status="open",
                    created_tx=raw["transactionHash"].to_0x_hex(),
                    created_block=raw["blockNumber"],
                )
                s.add(m)
            return m

        m = s.get(Match, a["id"])
        if m is None:  # started indexing after creation; skip
            return None

        if name == "MatchJoined" and m.status == "open":
            m.joiner = a["joiner"]
            m.joiner_move = MOVE_NAMES[a["joinerMove"]]
            m.reveal_deadline = a["revealDeadline"]
            m.status = "joined"
        elif name == "MatchSettled" and m.status != "settled":
            m.status = "settled"
            m.creator_move = MOVE_NAMES[a["creatorMove"]]
            m.joiner_move = MOVE_NAMES[a["joinerMove"]]
            m.forfeit = a["forfeit"]
            c, j = self._player(s, m.creator), self._player(s, m.joiner)
            if a["winner"] == ZERO:
                m.is_draw = True
                c.draws += 1
                j.draws += 1
                update_elo(c, j, draw=True)
            else:
                m.winner = a["winner"]
                w, l = (c, j) if a["winner"] == m.creator else (j, c)
                w.wins += 1
                l.losses += 1
                update_elo(w, l)
        elif name == "MatchCancelled":
            m.status = "cancelled"
        return m


if __name__ == "__main__":
    from .chain import get_contract, get_web3
    from .db import make_session_factory

    logging.basicConfig(level=logging.INFO)
    w3 = get_web3(config.RPC_URL)
    idx = Indexer(w3, get_contract(w3, config.CONTRACT_ADDRESS), make_session_factory())

    async def print_change(m: dict):
        log.info("match %s -> %s", m["id"], m["status"])

    asyncio.run(idx.run(print_change))
