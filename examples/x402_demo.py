"""Watch the pause stop a real x402 payment.

x402.org/protected is the x402 project's public demo. It asks for $0.01 of
test USDC on Base Sepolia, a test network. You need no funds to run this:
the key below is made on the spot and owns nothing, so the worst outcome is
a signed payment that the seller cannot collect.

    pip install "pay-with-a-pause[x402] @ git+https://github.com/akshaychugh-xyz/pause-and-pay"
    export SEARCHAPI_API_KEY=...
    python examples/x402_demo.py
"""

import asyncio

from eth_account import Account
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.http.utils import decode_payment_required_header
from x402.mechanisms.evm.exact import register_exact_evm_client

from pause import approve, load_receipt
from pause.x402_hook import add_pause

URL = "https://x402.org/protected"


async def main() -> None:
    client = register_exact_evm_client(x402Client(), Account.create())

    async with x402HttpxClient(add_pause(client)) as http:
        print(f"1. The agent asks for {URL}. The seller answers 402 and the pause runs.")
        try:
            response = await http.get(URL)
            print(f"   The pause said proceed. Seller answered {response.status_code}.")
            return
        except Exception as refusal:
            print(f"   Nothing was signed. The agent gets:\n   {refusal}\n")
            receipt_id = str(refusal).split("receipt ")[1].split(")")[0]

        print(f"2. You read the receipt and decide it's fine: pause approve {receipt_id}")
        approve(load_receipt(receipt_id))   # stands in for you typing that command

        print("3. The agent retries. This time the client signs and sends the payment.")
        response = await http.get(URL)
        why = ""
        if response.status_code == 402:   # expected: the throwaway key has no test USDC
            why = f": {decode_payment_required_header(response.headers['payment-required']).error}"
        print(f"   Seller answered {response.status_code}{why}")


if __name__ == "__main__":
    asyncio.run(main())
