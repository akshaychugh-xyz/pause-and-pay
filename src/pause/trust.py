"""Trust check: what does the outside world say about this store?

The store's own website is the seller's pitch. This asks Google instead,
through SearchApi:

  google_about_this_store   rating, review count, returns, shipping, badges
  google_about_this_domain  how long Google has known the domain (only when
                            the store has no reviews to go on)
  google                    the brand's real site (only for a possible lookalike)
"""

from __future__ import annotations

import re
from datetime import date

from .models import StoreReport, registrable_domain
from .searchapi import SearchApi, SearchApiError

_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def check_store(api: SearchApi, domain: str, country: str = "us",
                brand: str | None = None, deep: bool = True, thin_below: int = 50) -> StoreReport:
    """One call for a store Google knows. Up to two more for one it doesn't.

    `deep=False` skips the follow-up calls; used when checking an alternative
    seller, where "no reviews" simply means "don't suggest it".
    """
    domain = registrable_domain(domain)
    report = StoreReport(domain=domain)
    try:
        data = api.search("google_about_this_store", domain=domain, gl=country)
        store = data.get("store")
        if store:
            _read_store_profile(report, store)
        if deep and (report.rating is None or (report.reviews_count or 0) < thin_below):
            # Few or no reviews to judge by. Fall back to: how long has Google known this site?
            age = api.search("google_about_this_domain", domain=domain, gl=country)
            report.first_indexed = (age.get("site_first_indexed_by_google") or {}).get("snippet")
        if deep and not report.known and brand:
            report.lookalike_of = _lookalike_of(api, domain, brand, country)
    except SearchApiError as exc:
        report.error = str(exc)
    return report


def _read_store_profile(report: StoreReport, store: dict) -> None:
    report.known = True
    report.title = store.get("title")
    report.rating = store.get("rating")
    report.reviews_count = store.get("reviews_count")
    report.badge = store.get("badge")
    report.summary = store.get("ai_overview") if isinstance(store.get("ai_overview"), str) else None
    report.return_window_days = (store.get("return_policy") or {}).get("window_days")
    report.sells = _business_hint(store.get("description") or "")

    for insight in store.get("insights") or []:
        if insight.get("name") == "Shipping" and insight.get("description"):
            report.shipping_note = insight["description"]
            days = [int(n) for n in re.findall(r"\d+", insight["description"]) if int(n) < 60]
            if days and "day" in insight["description"]:
                report.shipping_days_max = max(days)

    for topic in store.get("rating_details") or []:
        report.mentions[topic["name"]] = topic.get("mentions") or 0
        for sentiment in topic.get("sentiments") or []:
            if sentiment.get("label") == "poor":
                report.poor_sentiment[topic["name"]] = sentiment.get("percentage", 0)

    for other in store.get("other_ratings") or []:
        report.other_ratings[other["name"]] = other.get("rating_label", "")
        if "scamadviser" in other["name"].lower() and other.get("rating") is not None:
            report.scamadviser = int(other["rating"])


def _business_hint(description: str) -> str | None:
    """A store can be well rated and still not sell what the agent assumes.
    Kept deliberately narrow: descriptions are marketing copy, and broader
    keyword matching ("refurbished") misfires on big retailers."""
    if re.search(r"lease[- ]to[- ]own|rent[- ]to[- ]own", description.lower()):
        return "lease-to-own"
    return None


def _lookalike_of(api: SearchApi, domain: str, brand: str, country: str) -> str | None:
    """'sony-outlet-deals.shop' selling Sony: is that Sony's site?

    Only asked when the brand's name is inside a domain Google has no store
    profile for. Returns the brand's real domain if this one isn't it.
    """
    brand_key = re.sub(r"[^a-z0-9]", "", brand.lower())
    if len(brand_key) < 3 or brand_key not in re.sub(r"[^a-z0-9]", "", domain.split(".")[0]):
        return None
    results = api.search("google", q=f"{brand} official site", gl=country)
    official = [registrable_domain(r["link"]) for r in (results.get("organic_results") or [])[:3]
                if r.get("link")]
    if official and domain not in official:
        return official[0]
    return None


def domain_age_years(first_indexed: str | None, today: date | None = None) -> float | None:
    """Parse Google's sentence. 'more than 10 years ago' -> 10.0,
    'first indexed by Google in October 2019' -> years since then."""
    if not first_indexed:
        return None
    today = today or date.today()
    text = first_indexed.lower()
    more_than = re.search(r"more than (\d+) years? ago", text)
    if more_than:
        return float(more_than.group(1))
    dated = re.search(r"in (?:(%s) )?(\d{4})" % "|".join(_MONTHS), text)
    if dated:
        month = _MONTHS.index(dated.group(1)) + 1 if dated.group(1) else 1
        return round(((today.year - int(dated.group(2))) * 12 + today.month - month) / 12, 1)
    return None
