"""FastAPI app: REST endpoints, live WebSocket feed, and the static frontend.

Run:  uvicorn backend.api:app --reload
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_, select
from web3 import Web3

from . import config
from .chain import ABI, ROOT, get_contract, get_web3
from .db import Match, Player, make_session_factory
from .indexer import Indexer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")


class Hub:
    """Tracks connected browsers and fans out match updates."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


def create_app(w3: Web3 | None = None, contract_address: str | None = None, session_factory=None,
               run_indexer: bool = True) -> FastAPI:
    address = contract_address or config.CONTRACT_ADDRESS
    Session = session_factory or make_session_factory()
    hub = Hub()
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if run_indexer and address:
            chain = w3 or get_web3(config.RPC_URL)
            idx = Indexer(chain, get_contract(chain, address), Session)
            state["indexer"] = idx

            async def on_change(m: dict):
                await hub.broadcast({"type": "match", "match": m})

            task = asyncio.create_task(idx.run(on_change))
        elif not address:
            log.warning("CONTRACT_ADDRESS not set: serving API without an indexer")
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Monad RPS Arena", lifespan=lifespan)

    @app.get("/api/config")
    def get_config():
        return {
            "chainId": config.CHAIN_ID,
            "chainName": config.CHAIN_NAME,
            "rpcUrl": config.PUBLIC_RPC_URL,
            "explorerUrl": config.EXPLORER_URL,
            "contractAddress": address,
            "abi": ABI,
        }

    @app.get("/api/matches")
    def list_matches(
        status: str | None = Query(None, pattern="^(open|joined|settled|cancelled)$"),
        player: str | None = None,
        limit: int = Query(50, le=200),
    ):
        q = select(Match).order_by(Match.id.desc()).limit(limit)
        if status:
            q = q.where(Match.status == status)
        if player:
            p = Web3.to_checksum_address(player)
            q = q.where(or_(Match.creator == p, Match.joiner == p))
        with Session() as s:
            return [m.to_dict() for m in s.scalars(q)]

    @app.get("/api/matches/{match_id}")
    def get_match(match_id: int):
        with Session() as s:
            m = s.get(Match, match_id)
            if not m:
                raise HTTPException(404, "match not found")
            return m.to_dict()

    @app.get("/api/leaderboard")
    def leaderboard(limit: int = Query(20, le=100)):
        with Session() as s:
            rows = s.scalars(select(Player).order_by(Player.elo.desc()).limit(limit))
            return [p.to_dict() for p in rows]

    @app.get("/api/players/{address}")
    def player(address: str):
        addr = Web3.to_checksum_address(address)
        with Session() as s:
            p = s.get(Player, addr)
            return p.to_dict() if p else {
                "address": addr, "elo": config.ELO_START, "wins": 0, "losses": 0, "draws": 0, "games": 0
            }

    @app.websocket("/ws")
    async def ws_feed(ws: WebSocket):
        await ws.accept()
        hub.clients.add(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive pings from the client
        except WebSocketDisconnect:
            hub.clients.discard(ws)

    app.state.hub = hub
    app.state.session_factory = Session
    app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app


app = create_app()
