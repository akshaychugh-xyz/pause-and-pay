"""A stand-in for SearchApi that answers from made-up data.

The stores here are invented. The JSON shapes copy the real responses, so the
parsing code is exercised without a network call or an API key.
"""

from __future__ import annotations

from pause.searchapi import Call, SearchApi, SearchApiError

ITEM = "Acme QC-900X Wireless Headphones"


def store_profile(title, rating, reviews, return_days=30, shipping="Free 2-4 day delivery",
                  scamadviser=95, description="Sells consumer electronics.", poor_shipping=5):
    return {"store": {
        "title": title, "description": description, "rating": rating, "reviews_count": reviews,
        "badge": "Top Quality Store" if (rating or 0) >= 4.5 else None,
        "return_policy": {"window_days": return_days} if return_days else None,
        "insights": [{"name": "Shipping", "description": shipping, "rating": 4}],
        "rating_details": [{"name": "Shipping", "mentions": 100, "sentiments": [
            {"label": "good", "percentage": 100 - poor_shipping},
            {"label": "poor", "percentage": poor_shipping}]}],
        "other_ratings": [{"name": "ScamAdviser trust score", "rating": scamadviser,
                           "rating_max": 100, "rating_label": f"{scamadviser}/100"}],
    }}


NO_RESULTS = {"error": "Google About This Store API didn't return any results."}

STORES = {
    "bigbox.test": store_profile("BigBox", 4.6, 1669),
    "pricey.test": store_profile("Pricey Electronics", 4.6, 246),
    "goodalt.test": store_profile("GoodAlt Camera", 4.9, 1217),
    "lowrated.test": store_profile("LowRated Camera", 2.7, 461),
    "lease.test": store_profile("LeaseCo", 4.5, 435, return_days=None,
                                description="Provides a lease-to-own service for electronics."),
    "tinybrand.test": store_profile("Tiny Brand", 4.0, 13),
    "shortreturns.test": store_profile("ShortReturns", 4.5, 9000, return_days=7),
    "slowship.test": store_profile("SlowShip", 4.4, 800, shipping="Free 5-6 day delivery"),
}

DOMAIN_AGE = {
    "tinybrand.test": {"site_first_indexed_by_google": {"snippet": "tinybrand.test was indexed by Google more than 10 years ago"}},
}


def shopping_result(position, title, seller, price, reviews=None, offers=None, condition=None):
    result = {"position": position, "title": title, "seller": seller, "price": f"${price}",
              "extracted_price": price, "product_token": f"token-{position}"}
    if reviews:
        result["reviews"] = reviews
    if offers:
        result["offers"] = offers
    if condition:
        result["condition"] = condition
    return result


SHOPPING = {"shopping_results": [
    shopping_result(1, "Acme QC-900X Wireless Headphones", "BigBox", 299.99, 15000, "& more"),
    shopping_result(2, "Acme QC-1000X Wireless Headphones", "BigBox", 399.99, 9000, "& more"),
    shopping_result(3, "Acme QC-900X Wireless Headphones", "Used Stuff", 150.00, condition="Pre-owned"),
    shopping_result(4, "Acme QC-900X Headphones Bundle with Stand", "Pricey Electronics", 399.99, 3),
    shopping_result(5, "Acme QC-900X Wireless Headphones", "GoodAlt Camera", 299.99, 15000),
    shopping_result(6, "Acme QC-900X Wireless Headphones", "LowRated Camera", 279.99, 15000),
]}


def offer(position, merchant, domain, total, title=ITEM, rating=None, reviews=None, details=()):
    raw = {"position": position, "title": title, "link": f"https://www.{domain}/p/qc900x",
           "price": f"${total}", "extracted_price": total, "total_price": f"${total}",
           "extracted_total_price": total, "details": list(details),
           "merchant": {"name": merchant}}
    if rating:
        raw.update(rating=rating, reviews=reviews)
    return raw


PRODUCT = {
    "product": {"title": ITEM, "brand": "Acme", "rating": 4.6, "reviews": 15000},
    "typical_prices": {"extracted_low_price": 250.0, "extracted_high_price": 371.0},
    "offers": [
        offer(1, "Marketplace - somebody", "ebay.com", 260.00),
        offer(2, "LeaseCo", "lease.test", 244.94, rating=4.5, reviews=435),
        offer(3, "LowRated Camera", "lowrated.test", 279.99, title=ITEM + " (Open Box)"),
    ],
}
MORE_OFFERS = {"offers": [
    offer(1, "Refurb Depot", "refurb.test", 288.99, rating=4.8, reviews=18000),
    offer(2, "BigBox", "bigbox.test", 299.99),
    offer(3, "GoodAlt Camera", "goodalt.test", 299.99, rating=4.9, reviews=1200),
    offer(4, "Pricey Electronics", "pricey.test", 399.99),
    offer(5, "Far Away Imports", "faraway.test", 492.76),
]}


class FakeApi(SearchApi):
    def __init__(self, down: bool = False):
        super().__init__(api_key="test")
        self.down = down

    def search(self, engine: str, **params) -> dict:
        if self.down:
            raise SearchApiError(f"{engine}: network unreachable")
        self.calls.append(Call(engine, f"search_{len(self.calls)}", 0.0))
        if engine == "google_about_this_store":
            return STORES.get(params["domain"], NO_RESULTS)
        if engine == "google_about_this_domain":
            return DOMAIN_AGE.get(params["domain"], NO_RESULTS)
        if engine == "google_shopping":
            return SHOPPING
        if engine == "google_product":
            return PRODUCT
        if engine == "google_product_offers":
            return MORE_OFFERS
        if engine == "google":
            return {"organic_results": [{"link": "https://www.acme.test/"},
                                        {"link": "https://shop.acme.test/headphones"}]}
        raise AssertionError(f"unexpected engine {engine}")
