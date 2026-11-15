"""The gate: pause() before pay(), enforced in code.

Telling an agent "be careful" in its prompt is a request. This is a lock:
pay() refuses to run without a token, and the only way to get a token is to
pass pause() (or have a human approve what pause() flagged). The token is
bound to one store, one item, one amount, and expires in minutes.

Every pause also writes a receipt: what was checked, what was found, and why
the verdict came out the way it did.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .deal import find_better_deal
from .models import (ASK_HUMAN, BLOCK, PROCEED, SUGGEST_SWITCH, DealReport, Policy,
                     PurchaseIntent, StoreReport, Verdict, registrable_domain)
from .judge import llm_decide, lookup_candidates
from .rubric import decide, switch_candidates
from .searchapi import SearchApi
from .trust import check_store


class PauseRequired(PermissionError):
    """pay() was called without a valid pause token."""


@dataclass
class Receipt:
    id: str
    created_at: str
    intent: PurchaseIntent
    tier: str                      # "skip", "trust" or "full"
    verdict: Verdict
    store: StoreReport | None
    deal: DealReport | None
    searches: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    token: str | None = None       # only set when the verdict is "proceed"
    judge: dict = field(default_factory=lambda: {"mode": "rules"})  # who decided, and what the rules said

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=list)


def pause(intent: PurchaseIntent, api: SearchApi | None = None,
          policy: Policy | None = None, llm_client=None) -> Receipt:
    """Check the store and the price, decide, write a receipt.

    `llm_client` is only used when policy.judge == "llm"; pass your own
    anthropic.Anthropic() to control credentials, or leave it to the default.
    """
    api = api or SearchApi()
    policy = policy or Policy()
    started, first_call = time.monotonic(), len(api.calls)
    known = intent.domain in policy.known_merchants
    judged_by: dict = {"mode": "rules"}

    store: StoreReport | None = None
    deal: DealReport | None = None
    if known and intent.price < policy.skip_below:
        tier = "skip"
        verdict = Verdict(PROCEED, ["Small purchase at a store you've used before. No pause needed."])
    else:
        tier = "full" if (intent.price >= policy.full_above or not known) else "trust"
        brand_guess = intent.item.split()[0] if intent.item.split() else None
        with ThreadPoolExecutor(max_workers=4) as pool:
            store_job = pool.submit(check_store, api, intent.domain, intent.country, brand_guess,
                                    True, policy.min_reviews_for_rating)
            deal_job = pool.submit(find_better_deal, api, intent) if tier == "full" else None
            store = store_job.result()
            deal = deal_job.result() if deal_job else None
            alt_stores: dict[str, StoreReport] = {}
            if deal and deal.matched:
                # Cheaper sellers get the same store check before anyone suggests them.
                offers = (lookup_candidates if policy.judge == "llm" else switch_candidates)(intent, deal, policy)
                domains = list(dict.fromkeys(o.domain for o in offers))[:3]
                lookups = pool.map(lambda d: check_store(api, d, intent.country, deep=False), domains)
                alt_stores = dict(zip(domains, lookups))
        verdict = decide(intent, store, deal, policy, alt_stores)
        if policy.judge == "llm":
            verdict, judged_by = llm_decide(intent, store, deal, policy, alt_stores, verdict, llm_client)

    receipt = Receipt(
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        intent=intent, tier=tier, verdict=verdict, store=store, deal=deal,
        searches=[asdict(call) for call in api.calls[first_call:]],
        seconds=round(time.monotonic() - started, 2), judge=judged_by,
    )
    if verdict.decision == PROCEED:
        receipt.token = _issue_token(receipt.id, intent.merchant_url, intent.item,
                                     intent.price, intent.currency, policy.token_ttl_seconds)
    _save(receipt)
    return receipt


def approve(receipt: Receipt, choice: str = "original", ttl_seconds: int = 600) -> str:
    """A human said yes. `choice` is "original" or "alternative".

    This is for the human's side of the conversation (CLI, UI). Don't hand it
    to the agent as a tool, or the agent can approve itself.
    """
    decision = receipt.verdict.decision
    if decision == BLOCK:
        raise PauseRequired("This purchase was blocked. Buy it yourself if you're sure.")
    intent = receipt.intent
    if choice == "alternative":
        if not receipt.verdict.alternative:
            raise ValueError("This receipt has no alternative to approve.")
        offer = receipt.verdict.alternative.offer
        return _issue_token(receipt.id, offer.link or offer.domain, intent.item,
                            offer.total_price, intent.currency, ttl_seconds)
    return _issue_token(receipt.id, intent.merchant_url, intent.item,
                        intent.price, intent.currency, ttl_seconds)


def pay(merchant_url: str, item: str, price: float, currency: str = "USD",
        pause_token: str | None = None) -> dict:
    """Mock payment. Swap the body for your real rail; keep the first line.

    The check is the same whatever the rail is: a card, a wallet, an
    agent-checkout protocol. The pause happens before any of them.
    """
    receipt_id = verify_token(pause_token, merchant_url, item, price, currency)
    return {
        "status": "paid (mock)",
        "merchant": registrable_domain(merchant_url),
        "item": item,
        "amount": price,
        "currency": currency,
        "pause_receipt": receipt_id,
    }


# ------------------------------------------------------------------ tokens

def _issue_token(receipt_id: str, merchant_url: str, item: str, price: float,
                 currency: str, ttl_seconds: int) -> str:
    claims = {
        "rid": receipt_id,
        "what": _fingerprint(merchant_url, item, price, currency),
        "exp": int(time.time()) + ttl_seconds,
    }
    body = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True).encode()).decode()
    return f"{body}.{_sign(body)}"


def verify_token(token: str | None, merchant_url: str, item: str, price: float,
                 currency: str) -> str:
    """Returns the receipt id, or raises PauseRequired saying what's wrong."""
    if not token or "." not in token:
        raise PauseRequired("No pause token. Call pause() first.")
    body, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(body)):
        raise PauseRequired("Pause token is not valid.")
    claims = json.loads(base64.urlsafe_b64decode(body))
    if claims["exp"] < time.time():
        raise PauseRequired("Pause token has expired. Pause again; prices and stores change.")
    if claims["what"] != _fingerprint(merchant_url, item, price, currency):
        raise PauseRequired("Pause token was issued for a different store, item or amount.")
    return claims["rid"]


def _fingerprint(merchant_url: str, item: str, price: float, currency: str) -> str:
    what = f"{registrable_domain(merchant_url)}|{' '.join(item.lower().split())}|{price:.2f}|{currency.upper()}"
    return hashlib.sha256(what.encode()).hexdigest()[:32]


def _sign(body: str) -> str:
    return hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()


def _secret() -> bytes:
    if os.environ.get("PAUSE_SECRET"):
        return os.environ["PAUSE_SECRET"].encode()
    path = home() / "secret"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip().encode()


# ---------------------------------------------------------------- receipts

def home() -> Path:
    return Path(os.environ.get("PAUSE_HOME", Path.home() / ".pause"))


def _save(receipt: Receipt) -> None:
    folder = home() / "receipts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{receipt.id}.json").write_text(receipt.to_json())


def load_receipt(receipt_id: str) -> Receipt:
    from .models import Alternative, Offer

    raw = json.loads((home() / "receipts" / f"{receipt_id}.json").read_text())
    verdict = raw["verdict"]
    alternative = verdict.pop("alternative")
    if alternative:
        alternative = Alternative(Offer(**alternative["offer"]), alternative["saving"],
                                  alternative["saving_pct"])
    deal = raw["deal"]
    if deal:
        deal["offers"] = [Offer(**offer) for offer in deal["offers"]]
    return Receipt(
        id=raw["id"], created_at=raw["created_at"], intent=PurchaseIntent(**raw["intent"]),
        tier=raw["tier"], verdict=Verdict(**verdict, alternative=alternative),
        store=StoreReport(**raw["store"]) if raw["store"] else None,
        deal=DealReport(**deal) if deal else None,
        searches=raw["searches"], seconds=raw["seconds"], token=raw["token"],
        judge=raw.get("judge") or {"mode": "rules"},
    )
