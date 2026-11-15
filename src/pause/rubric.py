"""The rubric: turn the reports into one of four verdicts.

  proceed         store looks fine, price is fair
  suggest_switch  same product, meaningfully cheaper, at a seller that passes
                  the same trust bar. Suggested to the human, never done silently.
  ask_human       something is unknown or off. Unknown is not "scam", and it
                  is not "fine" either.
  block           two independent bad signals. A human can still buy it
                  themselves; the agent won't.

It is plain if/else on purpose. Same inputs, same verdict, and every verdict
comes with the reasons that produced it.
"""

from __future__ import annotations

from .models import (ASK_HUMAN, BLOCK, PROCEED, SUGGEST_SWITCH, Alternative, DealReport, Offer,
                     Policy, PurchaseIntent, StoreReport, Verdict, is_marketplace)
from .trust import domain_age_years

_SYMBOLS = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}


def decide(intent: PurchaseIntent, store: StoreReport, deal: DealReport | None,
           policy: Policy, alt_stores: dict[str, StoreReport] | None = None) -> Verdict:
    """`alt_stores` holds the store reports for cheaper sellers found by the
    deal check (domain -> report), so an alternative is judged like the original."""
    blocks: list[str] = []   # enough on their own to stop the purchase
    flags: list[str] = []    # a human should look
    good: list[str] = []     # evidence in favour
    notes: list[str] = []    # checked, didn't change the outcome
    symbol = _SYMBOLS.get(intent.currency, intent.currency + " ")
    money = lambda amount: f"{symbol}{amount:,.2f}"

    usual = deal.median_price if deal and deal.matched else None
    below_usual = (1 - intent.price / usual) * 100 if usual else 0.0

    # ---------------------------------------------------------------- trust
    if store.error:
        flags.append(f"Could not check the store ({store.error}). Unknown is not the same as safe.")
    elif not store.known:
        seen = (f"domain history: {store.first_indexed}" if store.first_indexed
                else "Google has no record of the domain either")
        unknown = f"Google has no store profile for {store.domain} ({seen})."
        if store.lookalike_of:
            blocks.append(f"{unknown} It uses the brand's name, but the brand's own site "
                          f"is {store.lookalike_of}.")
        elif usual and intent.price < policy.scam_cheap_ratio * usual:
            blocks.append(f"{unknown} And the price is {below_usual:.0f}% below the usual "
                          f"{money(usual)}.")
        else:
            flags.append(unknown)
    else:
        name = store.title or store.domain
        if store.scamadviser is not None and store.scamadviser < policy.scamadviser_block_below:
            blocks.append(f"{name} has a ScamAdviser trust score of {store.scamadviser}/100.")
        if store.rating is None:
            age = domain_age_years(store.first_indexed)
            flags.append(f"{name} has no customer reviews on Google"
                         + (f" (domain known for {age:g}+ years)." if age else "."))
        elif (store.reviews_count or 0) < policy.min_reviews_for_rating:
            age = domain_age_years(store.first_indexed)
            thin = f"{name} is rated {store.rating:.1f}/5, but from only {store.reviews_count or 0} reviews"
            established = (round(store.rating, 1) >= policy.min_rating
                           and age is not None and age >= policy.established_years
                           and (store.scamadviser or 0) >= policy.established_scamadviser)
            if established:
                good.append(f"{thin}. The domain has been known for {age:g}+ years and "
                            f"ScamAdviser scores it {store.scamadviser}/100.")
            else:
                flags.append(thin + ".")
        elif round(store.rating, 1) < policy.min_rating:
            worst = max(store.poor_sentiment.items(), key=lambda kv: kv[1], default=None)
            flags.append(f"{name} is rated {store.rating:.1f}/5 from {store.reviews_count:,} reviews"
                         + (f"; {worst[1]}% of mentions of '{worst[0].lower()}' are negative." if worst else "."))
        else:
            good.append(f"{name}: rated {store.rating:.1f}/5 from {store.reviews_count:,} reviews"
                        + (f", {store.badge}" if store.badge else "") + ".")

        if is_marketplace(store.domain):
            notes.append("This is a marketplace: the rating is for the platform, not the seller.")
        if store.sells:
            flags.append(f"{name} describes itself as {store.sells}; "
                         "this may not be an outright purchase.")

        if intent.price >= policy.returns_matter_above:
            if store.return_window_days is None:
                notes.append("No return policy on record for this store.")
            elif store.return_window_days < policy.short_return_days:
                flags.append(f"Return window is only {store.return_window_days} days "
                             f"on a {money(intent.price)} purchase.")

        if intent.need_by_days is not None:
            poor_shipping = store.poor_sentiment.get("Shipping", 0)
            if store.shipping_days_max and store.shipping_days_max > intent.need_by_days:
                flags.append(f"You need it in {intent.need_by_days} days; this store ships in "
                             f"up to {store.shipping_days_max} ({store.shipping_note}).")
            elif poor_shipping >= policy.poor_shipping_pct:
                flags.append(f"You need it in {intent.need_by_days} days and {poor_shipping}% of "
                             f"shipping mentions for this store are negative.")

    # A store the buyer already uses has earned the benefit of the doubt.
    if store.domain in policy.known_merchants and flags:
        notes += [f"(You've bought here before.) {flag}" for flag in flags]
        flags = []

    # ----------------------------------------------------------------- deal
    alternative: Alternative | None = None
    if deal is None:
        notes.append("Price not compared (below the threshold for a full check).")
    elif not deal.matched:
        notes.append(f"Price not compared: {deal.error or 'no matching product found'}")
    else:
        title = deal.product_title
        if deal.merchant_price is None:
            notes.append(f"This store isn't among the sellers Google lists for '{title}'; "
                         "matched by title, so confirm it's the same item.")
        elif intent.price > deal.merchant_price * (1 + policy.own_listing_gap_pct / 100):
            flags.append(f"Google lists this same store at {money(deal.merchant_price)} for "
                         f"'{title}'. You are about to pay {money(intent.price)}.")
        elif deal.confidence != "high":
            notes.append(f"Google lists this store at {money(deal.merchant_price)} for '{title}'.")

        if usual and intent.price < policy.too_cheap_ratio * usual:
            flags.append(f"Price is {below_usual:.0f}% below the usual {money(usual)} for this "
                         "product. That usually means used, refurbished or a different variant.")

        alternative, skipped = _best_alternative(intent, deal, policy, alt_stores or {}, money)
        notes += skipped
        if alternative is None:
            if deal.typical_high and intent.price > deal.typical_high:
                flags.append(f"{money(intent.price)} is above Google's typical range "
                             f"({money(deal.typical_low)} to {money(deal.typical_high)}).")
            elif usual and intent.price <= usual * (1 + policy.min_saving_pct / 100):
                good.append(f"Price is in line: the usual price across {len(deal.offers)} "
                            f"sellers is {money(usual)}.")
            elif usual:
                notes.append(f"Price is {-below_usual:.0f}% above the usual {money(usual)}, but no "
                             "cheaper seller passed the checks.")

    # -------------------------------------------------------------- verdict
    if blocks:
        return Verdict(BLOCK, blocks + flags, good + notes)
    if alternative:
        offer, seller = alternative.offer, (alt_stores or {})[alternative.offer.domain]
        reason = (f"Same product is {money(alternative.saving)} ({alternative.saving_pct:.0f}%) cheaper "
                  f"at {offer.merchant}: {money(offer.total_price)} incl. delivery. That store is "
                  f"rated {seller.rating:.1f}/5 from {seller.reviews_count:,} reviews, "
                  f"{seller.return_window_days}-day returns.")
        return Verdict(SUGGEST_SWITCH, [reason] + flags, good + notes, alternative)
    if flags:
        return Verdict(ASK_HUMAN, flags, good + notes)
    return Verdict(PROCEED, good, notes)


def switch_candidates(intent: PurchaseIntent, deal: DealReport, policy: Policy) -> list[Offer]:
    """Offers cheap enough to be worth switching for, and plausibly the same
    thing. Cheapest first. The gate looks these stores up before decide() runs."""
    needed = max(policy.min_saving_abs, intent.price * policy.min_saving_pct / 100)
    return [offer for offer in deal.offers
            if intent.price - offer.total_price >= needed
            and offer.domain != intent.domain
            and _why_not_like_for_like(offer, deal, policy, intent) is None]


def _why_not_like_for_like(offer: Offer, deal: DealReport, policy: Policy,
                           intent: PurchaseIntent) -> str | None:
    if intent.condition == "new" and offer.condition_hint:
        return f"listed as {offer.condition_hint}"
    if deal.median_price and offer.total_price < policy.too_cheap_ratio * deal.median_price:
        return "too far below the usual price to be like-for-like"
    if offer.third_party:
        return "marketplace listing; seller and condition can't be checked"
    if intent.condition == "new" and not deal.sells_it_new(offer.merchant):
        return "can't confirm it sells this item new"
    return None


def _best_alternative(intent: PurchaseIntent, deal: DealReport, policy: Policy,
                      alt_stores: dict[str, StoreReport], money) -> tuple[Alternative | None, list[str]]:
    """Cheapest offer that is the same thing AND from a store that passes the
    same bar as the original. Cheapest is not best: most of this function is
    reasons to pass over an offer."""
    skipped: list[str] = []
    needed = max(policy.min_saving_abs, intent.price * policy.min_saving_pct / 100)
    for offer in deal.offers:  # sorted cheapest first
        saving = intent.price - offer.total_price
        if saving < needed:
            break
        if offer.domain == intent.domain:
            continue
        label = f"Cheaper at {offer.merchant} ({money(offer.total_price)}), but skipped"
        why_not = _why_not_like_for_like(offer, deal, policy, intent)
        seller = alt_stores.get(offer.domain)
        if why_not:
            skipped.append(f"{label}: {why_not}.")
        elif seller is None:
            skipped.append(f"{label}: store not checked.")
        elif not seller.known or seller.rating is None:
            skipped.append(f"{label}: no store reviews on Google.")
        elif round(seller.rating, 1) < policy.alt_min_rating or (seller.reviews_count or 0) < policy.alt_min_reviews:
            skipped.append(f"{label}: store rated {seller.rating:.1f}/5 from "
                           f"{seller.reviews_count or 0:,} reviews.")
        elif seller.sells:
            skipped.append(f"{label}: store is {seller.sells}.")
        elif seller.return_window_days is None:
            skipped.append(f"{label}: no return policy on record.")
        else:
            return Alternative(offer, round(saving, 2), round(saving / intent.price * 100, 1)), skipped
    return None, skipped
