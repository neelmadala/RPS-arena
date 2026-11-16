"""Session-key tests: a temporary key plays for a player from their in-contract balance."""
import pytest
from eth_account import Account
from web3 import Web3

from backend.chain import MOVES, make_commitment, new_salt, send_tx
from tests.test_contract import REVERT, STAKE, WINDOW, env, time_travel  # noqa: F401  (env is a fixture)

R, P, S = MOVES["rock"], MOVES["paper"], MOVES["scissors"]
GAS_FUND = Web3.to_wei(1, "ether")
HOUR = 3600


def start_session(w3, c, player, deposit=5 * STAKE, allowance=5 * STAKE, duration=HOUR):
    key = Account.create()
    c.functions.startSession(key.address, duration, allowance, GAS_FUND).transact(
        {"from": player, "value": deposit + GAS_FUND}
    )
    return key


def key_create(w3, c, key, player, move, stake=STAKE):
    salt = new_salt()
    rcpt = send_tx(w3, key, c.functions.createMatchFromBalance(make_commitment(c.address, player, move, salt), stake))
    return c.events.MatchCreated().process_receipt(rcpt)[0]["args"]["id"], salt


def test_session_setup_deposits_and_funds_key(env):
    w3, _, c, alice, *_ = env
    key = start_session(w3, c, alice)
    assert c.functions.credits(alice).call() == 5 * STAKE
    assert w3.eth.get_balance(key.address) == GAS_FUND
    k, expiry, allowance = c.functions.sessions(alice).call()
    assert k == key.address and allowance == 5 * STAKE and expiry > 0
    assert c.functions.sessionOwner(key.address).call() == alice


def test_session_key_plays_full_game_without_player_signing(env):
    w3, _, c, alice, bob, _ = env
    ka, kb = start_session(w3, c, alice), start_session(w3, c, bob)

    mid, salt = key_create(w3, c, ka, alice, R)
    m = c.functions.getMatch(mid).call()
    assert m[0] == alice  # the player, not the key, is the creator

    send_tx(w3, kb, c.functions.joinMatchFromBalance(mid, S))
    assert c.functions.getMatch(mid).call()[1] == bob

    a_wallet = w3.eth.get_balance(alice)
    rcpt = send_tx(w3, ka, c.functions.reveal(mid, R, salt))
    ev = c.events.MatchSettled().process_receipt(rcpt)[0]["args"]
    assert ev["winner"] == alice
    # winnings go back into the game balance, wallet untouched (key paid the gas)
    assert c.functions.credits(alice).call() == 6 * STAKE
    assert c.functions.credits(bob).call() == 4 * STAKE
    assert w3.eth.get_balance(alice) == a_wallet
    assert c.functions.sessions(alice).call()[2] == 4 * STAKE  # allowance spent


def test_mixed_funding_pays_each_side_its_own_way(env):
    """Bob joins with a normal wallet payment; Alice plays from her balance via a key."""
    w3, _, c, alice, bob, _ = env
    ka = start_session(w3, c, alice)
    mid, salt = key_create(w3, c, ka, alice, R)
    c.functions.joinMatch(mid, P).transact({"from": bob, "value": STAKE})
    b0 = w3.eth.get_balance(bob)
    send_tx(w3, ka, c.functions.reveal(mid, R, salt))
    assert w3.eth.get_balance(bob) - b0 == 2 * STAKE  # pushed to Bob's wallet
    assert c.functions.credits(alice).call() == 4 * STAKE


def test_draw_refunds_into_balance(env):
    w3, _, c, alice, bob, _ = env
    ka, kb = start_session(w3, c, alice), start_session(w3, c, bob)
    mid, salt = key_create(w3, c, ka, alice, P)
    send_tx(w3, kb, c.functions.joinMatchFromBalance(mid, P))
    send_tx(w3, ka, c.functions.reveal(mid, P, salt))
    assert c.functions.credits(alice).call() == 5 * STAKE
    assert c.functions.credits(bob).call() == 5 * STAKE
    assert w3.eth.get_balance(c.address) == 10 * STAKE


def test_allowance_and_balance_limits(env):
    w3, _, c, alice, *_ = env
    ka = start_session(w3, c, alice, deposit=5 * STAKE, allowance=2 * STAKE)
    key_create(w3, c, ka, alice, R)
    key_create(w3, c, ka, alice, R)
    with pytest.raises(REVERT):  # allowance used up
        key_create(w3, c, ka, alice, R)
    with pytest.raises(REVERT):  # player directly: limited only by balance
        c.functions.createMatchFromBalance(make_commitment(c.address, alice, R, new_salt()), 4 * STAKE).transact(
            {"from": alice}
        )
    c.functions.createMatchFromBalance(make_commitment(c.address, alice, R, new_salt()), 3 * STAKE).transact(
        {"from": alice}
    )
    assert c.functions.credits(alice).call() == 0


def test_key_cannot_withdraw_or_start_sessions(env):
    w3, _, c, alice, *_ = env
    ka = start_session(w3, c, alice)
    with pytest.raises(REVERT):
        send_tx(w3, ka, c.functions.withdraw())  # the key has no credits of its own
    with pytest.raises(REVERT):
        send_tx(w3, ka, c.functions.startSession(Account.create().address, HOUR, 0, 0))
    assert c.functions.credits(alice).call() == 5 * STAKE
    a0 = w3.eth.get_balance(alice)
    tx = c.functions.withdraw().transact({"from": alice})
    r = w3.eth.wait_for_transaction_receipt(tx)
    assert w3.eth.get_balance(alice) - a0 + r.gasUsed * r.effectiveGasPrice == 5 * STAKE


def test_expired_key_cannot_spend_but_can_still_reveal(env):
    w3, tester, c, alice, bob, _ = env
    ka = start_session(w3, c, alice, duration=WINDOW // 2)
    mid, salt = key_create(w3, c, ka, alice, R)
    c.functions.joinMatch(mid, S).transact({"from": bob, "value": STAKE})
    time_travel(w3, tester, WINDOW // 2 + 5)  # session expired, reveal window still open
    with pytest.raises(REVERT):
        key_create(w3, c, ka, alice, R)
    send_tx(w3, ka, c.functions.reveal(mid, R, salt))
    assert c.functions.credits(alice).call() == 6 * STAKE


def test_end_session_revokes_key(env):
    w3, _, c, alice, *_ = env
    ka = start_session(w3, c, alice)
    send_tx(w3, ka, c.functions.endSession())  # the key can end its own session
    assert c.functions.sessionOwner(ka.address).call() == "0x" + "0" * 40
    with pytest.raises(REVERT):
        key_create(w3, c, ka, alice, R)
    # a new session replaces the old one and keeps the balance
    kb = start_session(w3, c, alice, deposit=0)
    assert c.functions.credits(alice).call() == 5 * STAKE
    assert c.functions.sessions(alice).call()[0] == kb.address


def test_new_session_revokes_previous_key(env):
    w3, _, c, alice, *_ = env
    k1 = start_session(w3, c, alice)
    start_session(w3, c, alice, deposit=0)
    with pytest.raises(REVERT):
        key_create(w3, c, k1, alice, R)


def test_key_cannot_be_claimed_by_another_player(env):
    w3, _, c, alice, bob, _ = env
    ka = start_session(w3, c, alice)
    with pytest.raises(REVERT):
        c.functions.startSession(ka.address, HOUR, 0, 0).transact({"from": bob})


def test_key_claims_timeout_and_cancels_for_player(env):
    w3, tester, c, alice, bob, _ = env
    kb = start_session(w3, c, bob)
    salt = new_salt()
    tx = c.functions.createMatch(make_commitment(c.address, alice, R, salt)).transact({"from": alice, "value": STAKE})
    mid = c.events.MatchCreated().process_receipt(w3.eth.wait_for_transaction_receipt(tx))[0]["args"]["id"]
    send_tx(w3, kb, c.functions.joinMatchFromBalance(mid, P))
    time_travel(w3, tester, WINDOW + 5)
    send_tx(w3, kb, c.functions.claimTimeout(mid))
    assert c.functions.credits(bob).call() == 6 * STAKE

    mid2, _ = key_create(w3, c, kb, bob, S)
    send_tx(w3, kb, c.functions.cancelMatch(mid2))
    assert c.functions.credits(bob).call() == 6 * STAKE
