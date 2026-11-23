"""The pause, in front of an x402 payment.

x402 puts payment into HTTP. The server answers "402 Payment Required" with
what it wants paid, and the client signs a stablecoin payment and retries.
The official Python client runs hooks between those two steps. add_pause()
registers two of them, so the client cannot sign unless the pause said
"proceed" or a human approved this exact payment.

    from x402 import x402Client
    from x402.http.clients import x402HttpxClient
    from x402.mechanisms.evm.exact import register_exact_evm_client
    from pause.x402_hook import add_pause

    client = register_exact_evm_client(x402Client(), signer)
    async with x402HttpxClient(add_pause(client)) as http:
        response = await http.get("https://store.example/buy/123")

Give your agent `http`. Keep `client` and the signer away from it.

What gets checked is the site the request went to, not the site the 402
says it is for: the 402 is written by the party asking for money.

Needs: pip install "pay-with-a-pause[x402]"
"""

from __future__ import annotations

import asyncio
import importlib
import re
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse

from .gate import clear_to_pay
from .models import Policy, PurchaseIntent, registrable_domain
from .searchapi import SearchApi

# (the 402 as the client parsed it, the URL the request actually went to)
_last_402: ContextVar[tuple[Any, str] | None] = ContextVar("pause_last_402", default=None)


def add_pause(client, policy: Policy | None = None, api: SearchApi | None = None,
              llm_client=None, compare_prices: bool = False):
    """Put the pause in front of an x402 client. Returns the HTTP client to use.

    `client` is an x402Client or x402ClientSync with its payment schemes
    registered. Pass what comes back to x402HttpxClient, x402_requests or the
    x402 transports, in place of `client`.

    `compare_prices` is off by default because most things sold over x402
    (API calls, data, downloads) have no Google Shopping listing to compare
    against. Turn it on when the client buys products.
    """
    from x402 import AbortResult
    from x402.client import x402ClientSync
    from x402.http import x402HTTPClient, x402HTTPClientSync

    def remember_url(context):
        # Runs when the 402 arrives. Only here does the client say which URL it asked.
        _last_402.set((context.payment_required, context.request_url))

    def before_signing(context):
        refusal = _check(context, policy, api, llm_client, compare_prices)
        return AbortResult(refusal) if refusal else None

    if isinstance(client, x402ClientSync):
        client.on_before_payment_creation(before_signing)
        return x402HTTPClientSync(client).on_payment_required(remember_url)

    async def before_signing_off_the_loop(context):
        # A pause is a few seconds of searches; keep the event loop free.
        return await asyncio.to_thread(before_signing, context)

    client.on_before_payment_creation(before_signing_off_the_loop)
    return x402HTTPClient(client).on_payment_required(remember_url)


def _check(context, policy, api, llm_client, compare_prices) -> str | None:
    """None lets the client sign. A string stops it and says why."""
    required, chosen = context.payment_required, context.selected_requirements
    seen = _last_402.get()
    url = seen[1] if seen and seen[0] is required else ""
    if not url:
        return "Pause: can't tell which site is asking for this payment. Not signing."

    price = _usd(chosen)
    if price is None:
        return (f"Pause: can't price asset {chosen.asset} on {chosen.network} in US dollars. "
                "Not signing.")

    item = (_one_line(_description(required, chosen)) or urlparse(url).path.strip("/")
            or registrable_domain(url))
    return clear_to_pay(PurchaseIntent(url, item, price), api, policy, llm_client, compare_prices)


def _description(required, chosen) -> str | None:
    """What the seller says this payment is for. v2 and v1 keep it in different places.

    The 402 also carries a resource URL. It is ignored: sellers behind a proxy
    report a different host there, and a dishonest one can write any host it likes.
    """
    resource = getattr(required, "resource", None)
    if resource is not None:                                   # v2
        return resource.description or resource.service_name
    return getattr(chosen, "description", None)                # v1


def _usd(chosen) -> float | None:
    """The amount in dollars, if it is in a dollar stablecoin the x402 SDK lists."""
    amount = chosen.get_amount()
    if not amount.isdigit():
        return None
    for family in ("evm", "svm"):
        try:
            assets = importlib.import_module(f"x402.mechanisms.{family}.default_assets")
        except ImportError:        # that chain family's extra isn't installed
            continue
        known = assets.find_default_asset(chosen.asset, chosen.network)
        if known:
            return int(amount) / 10 ** known["decimals"]
    return None


def _one_line(text: str | None, limit: int = 120) -> str:
    """The seller wrote this text. Keep it short, plain and on one line."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.,&'()+/:%$-]", "", text or "")).strip()[:limit]
