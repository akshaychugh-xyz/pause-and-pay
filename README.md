# Pay with a pause

A checkpoint an AI agent has to pass before it pays.

When a person buys something online, they do a quick check without thinking about it: is this store real, and is this a fair price? An agent with a card skips that step. This project puts it back, as code the agent can't talk its way around:

1. **Is the store trustworthy?** Rating, review count, return window, shipping, and whether Google has ever heard of the domain.
2. **Is there a better deal?** The same product at other sellers, with delivery included, and whether those sellers pass the same trust check.
3. **Decide.** Proceed, suggest a switch, ask the human, or block. Every verdict lists its reasons.

`pay()` refuses to run without a token from `pause()`. The token is tied to one store, one item and one amount, and expires in ten minutes.

The data comes from [SearchApi](https://www.searchapi.io/?utm_source=dev&utm_medium=ambassador&utm_campaign=akshaychugh.xyz), which returns Google's store, shopping and product pages as JSON.

> SearchApi sponsored this project and gave me API credits. The code and the opinions in the rubric are mine. This is not an official SearchApi project.

## Try it

You need Python 3.10+ and a SearchApi key. [Sign up here](https://www.searchapi.io/?utm_source=dev&utm_medium=ambassador&utm_campaign=akshaychugh.xyz): the free tier has 100 requests, which is about 20 full pauses.

```bash
git clone https://github.com/akshaychugh-xyz/pay-with-a-pause
cd pay-with-a-pause
pip install -e .
export SEARCHAPI_API_KEY=your_key
```

The core has no dependencies outside the standard library.

```bash
pause check --url https://www.brandsmartusa.com \
  --item "Sony WH-1000XM5 Wireless Noise Canceling Headphones" --price 399.99
```

```text
PAUSE   brandsmartusa.com | Sony WH-1000XM5 Wireless Noise Canceling Headphones | 399.99 USD
VERDICT SUGGEST SWITCH

Why:
  - Same product is $100.00 (25%) cheaper at Best Buy: $299.99 incl. delivery. That store is
    rated 4.6/5 from 1,669 reviews, 15-day returns.

Also checked:
  - BrandsMart USA: rated 4.6/5 from 246 reviews, Top Quality Store.
  - Cheaper at FlexShopper ($244.94), but skipped: can't confirm it sells this item new.
  - Cheaper at eBay ($249.99), but skipped: marketplace listing; seller and condition can't be checked.
  - Cheaper at Beachcamera ($279.99), but skipped: listed as open box.
  - Cheaper at Reebelo USA ($288.99), but skipped: can't confirm it sells this item new.

Searches: 6 (google_about_this_store, google_shopping, google_product, google_product_offers,
          google_about_this_store, google_about_this_store)
```

Prices and ratings are live, so your output will differ.

## How it works

```text
agent wants to pay
        |
        v
   +---------+   trust check  -> google_about_this_store  (+ google_about_this_domain,
   |  pause  |                                              google, only for unknown stores)
   +---------+   deal check   -> google_shopping -> google_product + google_product_offers
        |
        v
     decide()  ->  proceed          token issued, pay() will run
                   suggest_switch   same product, cheaper, at a seller that passes the same check
                   ask_human        something is unknown or off
                   block            two independent bad signals
```

| File | What it does |
|---|---|
| `src/pause/trust.py` | `check_store()`: what Google knows about the store |
| `src/pause/deal.py` | `find_better_deal()`: the same product at other sellers |
| `src/pause/rubric.py` | `decide()`: plain if/else that turns the two reports into a verdict |
| `src/pause/gate.py` | `pause()`, the token, and a mock `pay()` that refuses without one |
| `src/pause/searchapi.py` | a 100-line SearchApi client |

A full pause is 4 to 6 searches. One search is one SearchApi credit.

### The rubric

These are opinions. They live in `Policy` in `src/pause/models.py`; change them.

| Signal | Result |
|---|---|
| Known store, under $50 | no pause at all |
| Known store, $50 to $100 | trust check only |
| New store, or $100 and up | trust and deal check |
| Store rating below 3.5 (50+ reviews) | ask the human |
| No store profile on Google | ask the human |
| No profile, and the domain uses the brand's name | block |
| No profile, and the price is 40%+ below usual | block |
| Price 25%+ below usual at any store | ask the human (often used or refurbished) |
| Return window under 14 days on $100+ | ask the human |
| Urgent, and the store ships slower than needed | ask the human |
| Same product 10%+ cheaper at a seller rated 4.0+ (100+ reviews) with a return policy | suggest a switch |
| Search failed | ask the human, never proceed |

Three things the rubric will not do:

- **Switch sellers on its own.** A cheaper seller is a suggestion for the human.
- **Treat missing data as a scam.** Unknown means ask.
- **Trust the cheapest offer.** Google's offer data has no "refurbished" field, so an alternative only counts if the seller also shows up when Google Shopping is filtered to new items.

## Use it in your agent

```python
from pause import PurchaseIntent, pause, pay

intent = PurchaseIntent("https://www.bestbuy.com", "Sony WH-1000XM5 headphones", 299.99)
receipt = pause(intent)

if receipt.verdict.decision == "proceed":
    pay(intent.merchant_url, intent.item, intent.price, pause_token=receipt.token)
else:
    print(receipt.verdict.reasons)   # show the human, then `pause approve <receipt id>`
```

`pay()` is a mock. Replace its body with your real payment call and keep the token check. The pause runs before the payment, so it works the same with a card, a wallet or an agent checkout protocol.

Every pause writes a receipt to `~/.pause/receipts/`: what was checked, each SearchApi search id, the offers it saw, and why it decided what it did.

### As an MCP server

```bash
pip install -e ".[mcp]"
claude mcp add pause -e SEARCHAPI_API_KEY=your_key -- pause-mcp
```

The agent gets two tools, `pause_before_pay` and `pay`. There is no approve tool on purpose. When the verdict is not "proceed", the human runs `pause approve <receipt id>` in a terminal and hands the token to the agent.

## Scorecard

`cases/cases.json` holds purchase scenarios with the verdict a careful person would expect. Run them against live data:

```bash
pause scorecard cases/cases.json
```

The last run is in [SCORECARD.md](SCORECARD.md). The risky stores in it are made-up `.example` domains. I'm not going to label a real store as a scam in a test file.

Offline tests use invented stores and need no key:

```bash
pip install -e ".[dev]" && pytest
```

## Limits

- Search results are a signal, not proof. A new honest store looks the same as a new dishonest one: unknown.
- Matching the agent's item to a Google product listing is a guess from the title. The receipt says how confident it is.
- Offer data doesn't say if an item is used, refurbished or leased. The rubric is conservative here and will miss some real deals.
- Coupons and equivalent products are not checked.
- Thresholds are in dollars. For other currencies, scale them with `Policy().scaled(85)`.

## License

MIT
