"""Indexer + API + bot against an in-memory chain. Run: pytest -q"""
import pytest
from eth_account import Account
from fastapi.testclient import TestClient
from web3 import EthereumTesterProvider, Web3

from backend.api import create_app
from backend.chain import ABI, BYTECODE, MOVES, make_commitment, new_salt, send_tx
from backend.db import make_session_factory
from backend.indexer import Indexer

STAKE = Web3.to_wei(0.01, "ether")
R, P, S = MOVES["rock"], MOVES["paper"], MOVES["scissors"]


@pytest.fixture
def env(tmp_path):
    provider = EthereumTesterProvider()
    w3 = Web3(provider)
    alice, bob = w3.eth.accounts[:2]
    tx = w3.eth.contract(abi=ABI, bytecode=BYTECODE).constructor(600).transact({"from": alice})
    addr = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    c = w3.eth.contract(address=addr, abi=ABI)
    Session = make_session_factory(f"sqlite:///{tmp_path / 'test.db'}")
    idx = Indexer(w3, c, Session, start_block=0, chunk=5, confirmations=0)
    return w3, c, Session, idx, alice, bob


def play(w3, c, creator, joiner, cmove, jmove):
    salt = new_salt()
    tx = c.functions.createMatch(make_commitment(c.address, creator, cmove, salt)).transact(
        {"from": creator, "value": STAKE}
    )
    mid = c.events.MatchCreated().process_receipt(w3.eth.wait_for_transaction_receipt(tx))[0]["args"]["id"]
    c.functions.joinMatch(mid, jmove).transact({"from": joiner, "value": STAKE})
    c.functions.reveal(mid, cmove, salt).transact({"from": creator})
    return mid


def test_indexer_and_api(env):
    w3, c, Session, idx, alice, bob = env
    m1 = play(w3, c, alice, bob, R, S)  # alice wins
    m2 = play(w3, c, bob, alice, P, P)  # draw
    salt = new_salt()
    c.functions.createMatch(make_commitment(c.address, alice, R, salt)).transact({"from": alice, "value": STAKE})

    changed = idx.sync_once()
    assert {m["id"] for m in changed} == {1, 2, 3}
    assert idx.sync_once() == []  # idempotent: nothing new

    app = create_app(w3=w3, contract_address=c.address, session_factory=Session, run_indexer=False)
    client = TestClient(app)

    assert client.get("/api/config").json()["contractAddress"] == c.address
    r = client.get(f"/api/matches/{m1}").json()
    assert r["status"] == "settled" and r["winner"] == alice
    assert r["creator_move"] == "rock" and r["joiner_move"] == "scissors"
    assert client.get(f"/api/matches/{m2}").json()["is_draw"] is True

    open_matches = client.get("/api/matches?status=open").json()
    assert [m["id"] for m in open_matches] == [3]
    assert len(client.get(f"/api/matches?player={bob}").json()) == 2

    board = client.get("/api/leaderboard").json()
    assert board[0]["address"] == alice and board[0]["elo"] > 1200
    assert board[0]["wins"] == 1 and board[0]["draws"] == 1
    assert client.get(f"/api/players/{bob}").json()["losses"] == 1
    assert client.get("/").status_code == 200  # frontend served


def test_bot_hosts_and_reveals(env, monkeypatch, tmp_path):
    w3, c, Session, idx, alice, bob = env
    bot_acct = Account.create()
    w3.eth.send_transaction({"from": alice, "to": bot_acct.address, "value": Web3.to_wei(1, "ether")})

    import scripts.bot as botmod
    monkeypatch.setenv("BOT_PRIVATE_KEY", bot_acct.key.hex())
    monkeypatch.setenv("BOT_STAKE", "0.01")
    monkeypatch.setattr(botmod, "SECRETS", tmp_path / "secrets.json")
    monkeypatch.setattr(botmod, "get_web3", lambda *_: w3)
    monkeypatch.setattr(botmod.config, "CONTRACT_ADDRESS", c.address)

    bot = botmod.Bot(host=True, join=True)
    bot.ensure_open_match()
    bot.ensure_open_match()  # should not create a second one
    assert c.functions.nextMatchId().call() == 2
    mid = 1

    c.functions.joinMatch(mid, R).transact({"from": bob, "value": STAKE})
    bot.reveal_joined()
    assert c.functions.getMatch(mid).call()[6] == 3  # settled

    # Bot joins a human's open match
    salt = new_salt()
    c.functions.createMatch(make_commitment(c.address, alice, P, salt)).transact({"from": alice, "value": STAKE})
    bot.join_open_matches()
    assert c.functions.getMatch(2).call()[6] == 2  # joined by bot
    assert c.functions.getMatch(2).call()[1] == bot_acct.address

    idx.sync_once()
    with Session() as s:
        from backend.db import Match
        assert s.get(Match, 1).status == "settled"


def test_send_tx_helper(env):
    w3, c, *_ , alice, _ = env
    acct = Account.create()
    w3.eth.send_transaction({"from": alice, "to": acct.address, "value": Web3.to_wei(1, "ether")})
    salt = new_salt()
    rcpt = send_tx(w3, acct, c.functions.createMatch(make_commitment(c.address, acct.address, S, salt)), STAKE)
    assert rcpt.status == 1


def test_live_websocket_push(env):
    """Indexer running inside the API pushes new matches to connected browsers."""
    w3, c, Session, idx, alice, bob = env
    app = create_app(w3=w3, contract_address=c.address, session_factory=Session, run_indexer=True)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        salt = new_salt()
        c.functions.createMatch(make_commitment(c.address, alice, R, salt)).transact({"from": alice, "value": STAKE})
        w3.provider.ethereum_tester.mine_blocks(3)  # get past CONFIRMATIONS
        msg = ws.receive_json()
        assert msg["type"] == "match" and msg["match"]["id"] == 1 and msg["match"]["status"] == "open"
