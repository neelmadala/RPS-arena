"""Contract tests on an in-memory chain (eth-tester / py-evm). Run: pytest -q"""
import pytest
from web3 import Web3, EthereumTesterProvider
from eth_tester.exceptions import TransactionFailed
from web3.exceptions import ContractLogicError

REVERT = (ContractLogicError, TransactionFailed)

from backend.chain import ABI, BYTECODE, MOVES, make_commitment, new_salt

STAKE = Web3.to_wei(1, "ether")
WINDOW = 600
R, P, S = MOVES["rock"], MOVES["paper"], MOVES["scissors"]


@pytest.fixture
def env():
    provider = EthereumTesterProvider()
    w3 = Web3(provider)
    alice, bob, carol = w3.eth.accounts[:3]
    factory = w3.eth.contract(abi=ABI, bytecode=BYTECODE)
    tx = factory.constructor(WINDOW).transact({"from": alice})
    addr = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    c = w3.eth.contract(address=addr, abi=ABI)
    return w3, provider.ethereum_tester, c, alice, bob, carol


def create(w3, c, player, move, stake=STAKE):
    salt = new_salt()
    commit = make_commitment(c.address, player, move, salt)
    tx = c.functions.createMatch(commit).transact({"from": player, "value": stake})
    rcpt = w3.eth.wait_for_transaction_receipt(tx)
    match_id = c.events.MatchCreated().process_receipt(rcpt)[0]["args"]["id"]
    return match_id, salt


def gas_cost(w3, tx):
    r = w3.eth.wait_for_transaction_receipt(tx)
    return r.gasUsed * r.effectiveGasPrice


def time_travel(w3, tester, seconds):
    tester.time_travel(w3.eth.get_block("latest").timestamp + seconds)
    tester.mine_blocks(1)


def test_python_commitment_matches_contract(env):
    w3, _, c, alice, *_ = env
    salt = new_salt()
    assert c.functions.makeCommitment(alice, R, salt).call() == make_commitment(c.address, alice, R, salt)


@pytest.mark.parametrize(
    "creator_move,joiner_move,expected",
    [(R, S, "creator"), (P, R, "creator"), (S, P, "creator"), (R, P, "joiner"), (S, R, "joiner"), (P, P, "draw")],
)
def test_outcomes_and_payouts(env, creator_move, joiner_move, expected):
    w3, _, c, alice, bob, _ = env
    mid, salt = create(w3, c, alice, creator_move)
    c.functions.joinMatch(mid, joiner_move).transact({"from": bob, "value": STAKE})

    a0, b0 = w3.eth.get_balance(alice), w3.eth.get_balance(bob)
    tx = c.functions.reveal(mid, creator_move, salt).transact({"from": alice})
    cost = gas_cost(w3, tx)
    ev = c.events.MatchSettled().process_receipt(w3.eth.get_transaction_receipt(tx))[0]["args"]

    a_gain = w3.eth.get_balance(alice) - a0 + cost
    b_gain = w3.eth.get_balance(bob) - b0
    if expected == "creator":
        assert ev["winner"] == alice and a_gain == 2 * STAKE and b_gain == 0
    elif expected == "joiner":
        assert ev["winner"] == bob and a_gain == 0 and b_gain == 2 * STAKE
    else:
        assert int(ev["winner"], 16) == 0 and a_gain == STAKE and b_gain == STAKE
    assert w3.eth.get_balance(c.address) == 0
    assert c.functions.getMatch(mid).call()[6] == 3  # Settled


def test_wrong_salt_or_move_rejected(env):
    w3, _, c, alice, bob, _ = env
    mid, salt = create(w3, c, alice, R)
    c.functions.joinMatch(mid, P).transact({"from": bob, "value": STAKE})
    with pytest.raises(REVERT):
        c.functions.reveal(mid, S, salt).transact({"from": alice})  # lying about the move
    with pytest.raises(REVERT):
        c.functions.reveal(mid, R, new_salt()).transact({"from": alice})


def test_commitment_cannot_be_copied_by_another_player(env):
    """Commitment includes the creator's address, so copying someone's hash is useless."""
    w3, _, c, alice, bob, carol = env
    salt = new_salt()
    commit = make_commitment(c.address, alice, R, salt)
    tx = c.functions.createMatch(commit).transact({"from": carol, "value": STAKE})
    mid = c.events.MatchCreated().process_receipt(w3.eth.wait_for_transaction_receipt(tx))[0]["args"]["id"]
    c.functions.joinMatch(mid, S).transact({"from": bob, "value": STAKE})
    with pytest.raises(REVERT):
        c.functions.reveal(mid, R, salt).transact({"from": carol})


def test_join_rules(env):
    w3, _, c, alice, bob, carol = env
    mid, _ = create(w3, c, alice, R)
    with pytest.raises(REVERT):
        c.functions.joinMatch(mid, P).transact({"from": bob, "value": STAKE - 1})
    with pytest.raises(REVERT):
        c.functions.joinMatch(mid, P).transact({"from": alice, "value": STAKE})
    with pytest.raises(REVERT):
        c.functions.joinMatch(mid, 0).transact({"from": bob, "value": STAKE})
    c.functions.joinMatch(mid, P).transact({"from": bob, "value": STAKE})
    with pytest.raises(REVERT):
        c.functions.joinMatch(mid, S).transact({"from": carol, "value": STAKE})


def test_timeout_forfeit(env):
    w3, tester, c, alice, bob, _ = env
    mid, salt = create(w3, c, alice, R)
    c.functions.joinMatch(mid, P).transact({"from": bob, "value": STAKE})
    with pytest.raises(REVERT):
        c.functions.claimTimeout(mid).transact({"from": bob})  # too early

    time_travel(w3, tester, WINDOW + 5)
    with pytest.raises(REVERT):
        c.functions.reveal(mid, R, salt).transact({"from": alice})  # too late
    with pytest.raises(REVERT):
        c.functions.claimTimeout(mid).transact({"from": alice})  # only joiner

    b0 = w3.eth.get_balance(bob)
    tx = c.functions.claimTimeout(mid).transact({"from": bob})
    assert w3.eth.get_balance(bob) - b0 + gas_cost(w3, tx) == 2 * STAKE
    ev = c.events.MatchSettled().process_receipt(w3.eth.get_transaction_receipt(tx))[0]["args"]
    assert ev["forfeit"] is True and ev["winner"] == bob


def test_cancel_open_match(env):
    w3, _, c, alice, bob, _ = env
    mid, _ = create(w3, c, alice, R)
    with pytest.raises(REVERT):
        c.functions.cancelMatch(mid).transact({"from": bob})
    a0 = w3.eth.get_balance(alice)
    tx = c.functions.cancelMatch(mid).transact({"from": alice})
    assert w3.eth.get_balance(alice) - a0 + gas_cost(w3, tx) == STAKE
    with pytest.raises(REVERT):
        c.functions.joinMatch(mid, P).transact({"from": bob, "value": STAKE})


def test_free_match(env):
    w3, _, c, alice, bob, _ = env
    mid, salt = create(w3, c, alice, S, stake=0)
    c.functions.joinMatch(mid, P).transact({"from": bob, "value": 0})
    tx = c.functions.reveal(mid, S, salt).transact({"from": alice})
    ev = c.events.MatchSettled().process_receipt(w3.eth.get_transaction_receipt(tx))[0]["args"]
    assert ev["winner"] == alice
