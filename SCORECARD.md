Snapshot from a live run on 15 November 2026. Prices and listings move, so a run today will differ: the cases that depend on a price (1, 3, 4 and 10) are pinned to that day's prices.

| # | Case | Verdict | As expected | Why (first reason) | Searches | Time |
|---|---|---|---|---|---|---|
| 1 | Big retailer, fair price | **Proceed** | yes | Best Buy: rated 4.6/5 from 1,669 reviews, Top Quality Store. | 4 | 6.9s |
| 2 | Brand's own store, fair price | **Proceed** | yes | Gymshark: rated 4.5/5 from 194 reviews, Top Quality Store. | 4 | 17.7s |
| 3 | Same headphones, $100 more than a trusted seller | **Suggest switch** | yes | Same product is $100.00 (25%) cheaper at Best Buy: $299.99 incl. delivery. That store is rated 4.6/5 from 1,669 reviews, 15-day returns. | 6 | 15.2s |
| 4 | Agent's price is higher than the store's own listing | **Suggest switch** | yes | Same product is $98.01 (25%) cheaper at Focus Camera: $299.99 incl. delivery. That store is rated 4.9/5 from 1,217 reviews, 30-day returns. | 5 | 3.2s |
| 5 | Store nobody has heard of (made-up domain) | **Ask the human** | yes | Google has no store profile for bargain-audio-warehouse.example (Google has no record of the domain either). | 5 | 2.7s |
| 6 | Unknown store AND a price too good to be true | **Block** | yes | Google has no store profile for bargain-audio-warehouse.example (Google has no record of the domain either). And the price is 60% below the usual $299.00. | 5 | 3.9s |
| 7 | Lookalike domain using the brand's name (made-up) | **Block** | yes | Google has no store profile for sony-outlet-store.example (Google has no record of the domain either). It uses the brand's name, but the brand's own site is sony.com. | 6 | 13.7s |
| 8 | $8 at a store you already use | **Proceed** | yes | Small purchase at a store you've used before. No pause needed. | 0 | 0.0s |
| 9 | $75 at a store you already use (trust check only) | **Proceed** | yes | Target: rated 4.5/5 from 1,982 reviews, Top Quality Store. | 1 | 1.2s |
| 10 | Outside the US: Indian retailer, priced in rupees | **Proceed** | yes | Nykaa: rated 4.2/5 from 431 reviews. | 4 | 3.7s |
| 11 | SearchApi unreachable | **Ask the human** | yes | Could not check the store (google_about_this_store: network unreachable (simulated)). Unknown is not the same as safe. | 0 | 0.0s |

11/11 as expected. 40 searches in total, 3.6 per pause on average. Pauses that searched live took 3.9s (median), 17.7s at worst.
