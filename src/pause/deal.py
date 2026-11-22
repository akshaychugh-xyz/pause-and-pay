"""Deal check: what does the same product cost elsewhere?

  google_shopping        find the product listing that matches the item
  google_product         Google's typical price range + the first sellers
  google_product_offers  more sellers, each with a total price incl. delivery

Google groups offers for the same product under one `product_token`, so
"the same product at another seller" is Google's judgement, not a guess from
titles. Matching the agent's item to the right listing is still a guess, and
the report says how confident it is.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from statistics import median

from .models import (DealReport, Offer, PurchaseIntent, is_marketplace, registrable_domain,
                     seller_key)
from .searchapi import SearchApi, SearchApiError

_NOT_NEW = {
    "refurbished": r"refurb|renewed|reconditioned",
    "open box": r"open[\s-]?box",
    "used": r"\bused\b|pre[\s-]?owned|second[\s-]?hand",
}
_BUNDLE = r"\bbundle\b|\bkit\b|\+| with "
MIN_SELLERS_FOR_USUAL_PRICE = 3
_CLOSE_ENOUGH = 0.05   # titles within this of the closest one count as equally close
_STOPWORDS = {"the", "a", "an", "and", "for", "with", "of", "in", "new"}


def find_better_deal(api: SearchApi, intent: PurchaseIntent) -> DealReport:
    report = DealReport()
    try:
        shopping = api.search(
            "google_shopping", q=intent.item, gl=intent.country,
            condition="new" if intent.condition == "new" else None,
        )
        results = shopping.get("shopping_results") or []
        listing = _pick_listing(results, intent)
        if listing is None:
            report.error = "No matching product listing on Google Shopping."
            return report

        # Page 1 of offers holds at most 3 sellers. If Google says there are
        # more, fetch page 2 at the same time instead of afterwards.
        token, country = listing["product_token"], intent.country
        with ThreadPoolExecutor(max_workers=2) as pool:
            product_job = pool.submit(api.search, "google_product", product_token=token, gl=country)
            more_job = (pool.submit(api.search, "google_product_offers", product_token=token,
                                    gl=country, page=2) if listing.get("offers") else None)
            product = product_job.result()
            raw_offers = list(product.get("offers") or [])
            if more_job:
                raw_offers += more_job.result().get("offers") or []
    except SearchApiError as exc:
        report.error = str(exc)
        return report

    report.matched = True
    report.product_title = (product.get("product") or {}).get("title") or listing.get("title")
    report.brand = (product.get("product") or {}).get("brand")
    typical = product.get("typical_prices") or {}
    report.typical_low = typical.get("extracted_low_price")
    report.typical_high = typical.get("extracted_high_price")
    report.offers = _read_offers(raw_offers)
    if intent.condition == "new":
        # Offers carry no "condition" field. The shopping search was filtered
        # to new items, so its sellers are the ones known to sell this new.
        models = {w for w in _words(intent.item) if _is_model_number(w)}
        report.new_sellers = sorted({
            seller_key(r["seller"]) for r in results
            if r.get("seller") and not r.get("condition") and models <= _words(r.get("title", ""))
        })

    # One or two sellers don't make a "usual price". Below three, the price
    # is reported as not compared rather than judged against a thin sample.
    like_for_like = [o.total_price for o in report.offers if not o.condition_hint]
    if len(like_for_like) >= MIN_SELLERS_FOR_USUAL_PRICE:
        report.median_price = round(median(like_for_like), 2)
    # "high" only if this very store is in the listing at about this price.
    own = [o for o in report.offers if o.domain == intent.domain]
    report.merchant_price = min((o.total_price for o in own), default=None)
    same_price = (report.merchant_price is not None
                  and abs(report.merchant_price - intent.price) <= 0.05 * intent.price)
    report.confidence = "high" if same_price else "medium"
    return report


def _pick_listing(results: list[dict], intent: PurchaseIntent) -> dict | None:
    """Choose the Google Shopping listing that is most likely the agent's item.

    Two steps, in this order:
    1. Closest title wins. "DualSense Wireless Controller" must not match
       "DualSense Edge Wireless Controller", a different product at twice the
       price, just because that listing has more reviews.
    2. Among titles that are equally close, prefer the listing with several
       sellers and the most reviews. That is usually the canonical product
       page, and it gives something to compare against.

    The agent's price is deliberately not used. If it were, a suspiciously
    cheap price would pull the match towards a cheaper variant and hide itself.
    """
    item_words = _words(intent.item)
    item_models = {w for w in item_words if _is_model_number(w)}
    merchant_key = re.sub(r"[^a-z0-9]", "", intent.domain.split(".")[0])
    item_is_bundle = bool(re.search(_BUNDLE, intent.item.lower()))

    candidates = []
    for result in results:
        if not result.get("product_token") or result.get("extracted_price") is None:
            continue
        title = result.get("title", "")
        if intent.condition == "new" and (result.get("condition") or _condition_hint(title)):
            continue
        words = _words(title)
        if item_models and not item_models <= words:
            continue  # a different model (XM6 is not XM5)
        if not item_models and len(item_words & words) / max(len(item_words), 1) < 0.6:
            continue

        seller_key = re.sub(r"[^a-z0-9]", "", (result.get("seller") or "").lower())
        usefulness = (
            0.6 * min(math.log10((result.get("reviews") or 0) + 1) / 4, 1.0)
            + (0.8 if result.get("offers") else 0.0)          # several sellers: something to compare
            + (0.2 if merchant_key and merchant_key in seller_key else 0.0)
            - (0.5 if re.search(_BUNDLE, title.lower()) and not item_is_bundle else 0.0)
        )
        candidates.append((_closeness(item_words, words), usefulness, result))

    if not candidates:
        return None
    closest = max(closeness for closeness, _, _ in candidates)
    shortlist = [c for c in candidates if c[0] >= closest - _CLOSE_ENOUGH]
    return max(shortlist, key=lambda c: (c[1], c[0]))[2]


def _closeness(item_words: set[str], title_words: set[str]) -> float:
    """1.0 when the two titles use the same words. Drops for item words the
    listing lacks ("Origin") and for words the listing adds ("Edge")."""
    shared = len(item_words & title_words)
    if not shared:
        return 0.0
    recall, precision = shared / len(item_words), shared / len(title_words)
    return 2 * recall * precision / (recall + precision)


def _read_offers(raw_offers: list[dict]) -> list[Offer]:
    offers, seen = [], set()
    for raw in raw_offers:
        total = raw.get("extracted_total_price") or raw.get("extracted_price")
        link = raw.get("link") or ""
        name = (raw.get("merchant") or {}).get("name") or ""
        if total is None or not link:
            continue
        key = (name, total)
        if key in seen:
            continue
        seen.add(key)
        reviews = raw.get("reviews")
        domain = registrable_domain(link)
        offers.append(Offer(
            merchant=name,
            domain=domain,
            total_price=float(total),
            rating=raw.get("rating"),
            reviews=int(reviews) if reviews is not None else None,
            title=raw.get("title", ""),
            details=list(raw.get("details") or []),
            link=link,
            condition_hint=_condition_hint(raw.get("title", "") + " " + " ".join(raw.get("details") or [])),
            third_party=" - " in name or is_marketplace(domain),
        ))
    return sorted(offers, key=lambda o: o.total_price)


def _condition_hint(text: str) -> str | None:
    for label, pattern in _NOT_NEW.items():
        if re.search(pattern, text.lower()):
            return label
    return None


def _words(text: str) -> set[str]:
    """Lowercase words; 'WH-1000XM5' becomes one word, 'wh1000xm5'."""
    raw = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())
    return {_normal(w.replace("-", "")) for w in raw} - _STOPWORDS


def _normal(word: str) -> str:
    """'cancelling' and 'canceling', 'headphones' and 'headphone' are one word.
    Model numbers are left alone."""
    if any(c.isdigit() for c in word):
        return word
    word = word.replace("ll", "l")
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _is_model_number(word: str) -> bool:
    return len(word) >= 4 and any(c.isdigit() for c in word) and any(c.isalpha() for c in word)
