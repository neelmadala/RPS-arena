"""A bot opponent with its own wallet, so there's always someone to play.

Modes (both on by default):
  host  keeps one open match at BOT_STAKE and reveals as soon as someone joins
  join  joins other players' open matches whose stake is <= BOT_MAX_STAKE

Salts are saved to bot_secrets.json before each commit, so restarting the bot never
causes a forfeit. Keep that file private.

Usage:
    python -m scripts.bot                 # host + join
    python -m scripts.bot --no-join       # only host
"""
import argparse
import json
import logging
import os
import random
import time

from eth_account import Account
from web3 import Web3

from backend import config
from backend.chain import ROOT, get_contract, get_web3, make_commitment, new_salt, send_tx

log = logging.getLogger("bot")
SECRETS = ROOT / os.getenv("BOT_SECRETS_FILE", "bot_secrets.json")
OPEN, JOINED = 1, 2
LOOKBACK = 200  # how many recent match ids to scan for open games


def load_secrets() -> dict:
    return json.loads(SECRETS.read_text()) if SECRETS.exists() else {}


def save_secrets(data: dict):
    SECRETS.write_text(json.dumps(data, indent=2))


class Bot:
    def __init__(self, host: bool, join: bool):
        key = os.getenv("BOT_PRIVATE_KEY")
        if not key:
            raise SystemExit("Set BOT_PRIVATE_KEY in .env (a separate testnet wallet with some MON).")
        self.w3 = get_web3(config.RPC_URL)
        self.c = get_contract(self.w3, config.CONTRACT_ADDRESS)
        self.acct = Account.from_key(key)
        self.host, self.join = host, join
        self.stake = Web3.to_wei(os.getenv("BOT_STAKE", "0.01"), "ether")
        self.max_stake = Web3.to_wei(os.getenv("BOT_MAX_STAKE", "0.05"), "ether")
        self.secrets = load_secrets()  # {"pending": {...}, "<match id>": {...}}
        log.info("Bot %s | host=%s join=%s", self.acct.address, host, join)
        self.recover_pending()

    def recover_pending(self):
        """If we crashed after sending createMatch but before saving its id, find it again."""
        pending = self.secrets.get("pending")
        if not pending:
            return
        commit = make_commitment(self.c.address, self.acct.address, pending["move"], bytes.fromhex(pending["salt"]))
        next_id = self.c.functions.nextMatchId().call()
        for mid in range(max(1, next_id - LOOKBACK), next_id):
            m = self.c.functions.getMatch(mid).call()
            if m[0] == self.acct.address and m[3] == commit:
                self.secrets[str(mid)] = self.secrets.pop("pending")
                log.info("Recovered secret for match #%s", mid)
                break
        else:
            self.secrets.pop("pending")  # the tx never landed
        save_secrets(self.secrets)

    def tx(self, fn, value=0):
        return send_tx(self.w3, self.acct, fn, value)

    # ---- hosting ------------------------------------------------------------
    def my_live_hosted(self) -> list[int]:
        return [int(k) for k in self.secrets if k.isdigit()]

    def ensure_open_match(self):
        for mid in self.my_live_hosted():
            if self.c.functions.getMatch(mid).call()[6] == OPEN:
                return  # already have one waiting
        move = random.randint(1, 3)
        salt = new_salt()
        # Save the secret *before* sending, so a crash mid-tx can't lose it.
        self.secrets["pending"] = {"move": move, "salt": salt.hex()}
        save_secrets(self.secrets)
        commit = make_commitment(self.c.address, self.acct.address, move, salt)
        receipt = self.tx(self.c.functions.createMatch(commit), value=self.stake)
        mid = self.c.events.MatchCreated().process_receipt(receipt)[0]["args"]["id"]
        self.secrets[str(mid)] = self.secrets.pop("pending")
        save_secrets(self.secrets)
        log.info("Hosting match #%s (stake %s MON)", mid, Web3.from_wei(self.stake, "ether"))

    def reveal_joined(self):
        for mid in self.my_live_hosted():
            m = self.c.functions.getMatch(mid).call()
            status, deadline = m[6], m[7]
            if status == JOINED and self.w3.eth.get_block("latest").timestamp > deadline:
                log.warning("Missed the reveal window for #%s; it will be forfeited", mid)
                del self.secrets[str(mid)]
                save_secrets(self.secrets)
            elif status == JOINED:
                s = self.secrets[str(mid)]
                self.tx(self.c.functions.reveal(mid, s["move"], bytes.fromhex(s["salt"])))
                log.info("Revealed match #%s", mid)
                del self.secrets[str(mid)]
                save_secrets(self.secrets)
            elif status not in (OPEN, JOINED):
                del self.secrets[str(mid)]  # settled/cancelled elsewhere
                save_secrets(self.secrets)

    # ---- joining ------------------------------------------------------------
    def join_open_matches(self):
        next_id = self.c.functions.nextMatchId().call()
        for mid in range(max(1, next_id - LOOKBACK), next_id):
            m = self.c.functions.getMatch(mid).call()
            creator, stake, status = m[0], m[2], m[6]
            if status == OPEN and creator != self.acct.address and stake <= self.max_stake:
                move = random.randint(1, 3)
                try:
                    self.tx(self.c.functions.joinMatch(mid, move), value=stake)
                    log.info("Joined match #%s with move %s", mid, move)
                except Exception as e:  # someone else probably joined first
                    log.info("Could not join #%s: %s", mid, e)

    def run(self):
        while True:
            try:
                if self.host:
                    self.reveal_joined()
                    self.ensure_open_match()
                if self.join:
                    self.join_open_matches()
            except Exception as e:
                log.warning("bot error: %s", e)
            time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-host", action="store_true")
    ap.add_argument("--no-join", action="store_true")
    a = ap.parse_args()
    Bot(host=not a.no_host, join=not a.no_join).run()
