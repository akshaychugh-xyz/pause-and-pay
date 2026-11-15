"""MCP server: gives any agent two tools, pause_before_pay and pay.

The agent can call pay() as often as it likes. Without a token from
pause_before_pay (or from a human running `pause approve`), it is refused.
There is deliberately no "approve" tool: approval belongs to the human.

Run:  pause-mcp            (after `pip install "pay-with-a-pause[mcp]"`)
Add:  claude mcp add pause -e SEARCHAPI_API_KEY=... -- pause-mcp
"""

from __future__ import annotations

from dataclasses import asdict

from mcp.server.mcpserver import MCPServer

from .gate import PauseRequired, pause
from .gate import pay as _pay
from .models import PurchaseIntent

mcp = MCPServer("pay-with-a-pause")


@mcp.tool()
def pause_before_pay(merchant_url: str, item: str, price: float, currency: str = "USD",
                     country: str = "us", need_by_days: int | None = None) -> dict:
    """Call this BEFORE paying for anything. Checks the store's reputation and
    whether the same product is cheaper at another trusted seller.

    Returns a verdict:
      proceed         -> a pause_token is included; pass it to pay().
      suggest_switch  -> show the user the cheaper seller and let them choose.
      ask_human       -> show the user the reasons and wait for their decision.
      block           -> do not buy. Tell the user why.
    For anything except "proceed", the user approves by running
    `pause approve <receipt_id>` themselves and giving you the token.
    """
    receipt = pause(PurchaseIntent(merchant_url, item, price, currency.upper(), country,
                                   need_by_days=need_by_days))
    alternative = receipt.verdict.alternative
    return {
        "verdict": receipt.verdict.decision,
        "reasons": receipt.verdict.reasons,
        "also_checked": receipt.verdict.notes,
        "alternative": asdict(alternative) if alternative else None,
        "receipt_id": receipt.id,
        "pause_token": receipt.token,
        "searches_used": len(receipt.searches),
        "seconds": receipt.seconds,
    }


@mcp.tool()
def pay(merchant_url: str, item: str, price: float, currency: str = "USD",
        pause_token: str = "") -> dict:
    """Pay for an item (mock: no money moves). Refuses without a valid
    pause_token for exactly this store, item and amount."""
    try:
        return _pay(merchant_url, item, price, currency.upper(), pause_token)
    except PauseRequired as exc:
        return {"status": "refused", "reason": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
