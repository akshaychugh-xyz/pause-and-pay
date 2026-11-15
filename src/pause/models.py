"""Plain data classes shared by the checks, the rubric and the gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

# Verdicts, from most to least permissive.
PROCEED = "proceed"
SUGGEST_SWITCH = "suggest_switch"
ASK_HUMAN = "ask_human"
BLOCK = "block"

# Platforms where the seller is someone else. A rating for the platform says
# little about the seller, and the listing may be used or refurbished.
_MARKETPLACE_NAMES = {
    "ebay", "mercari", "amazon", "walmart", "newegg", "etsy", "aliexpress", "temu",
    "poshmark", "facebook", "wish", "flipkart", "meesho", "rakuten", "stockx",
}


def is_marketplace(domain: str) -> bool:
    """True for amazon.com, amazon.in, ebay.co.uk, ..."""
    return registrable_domain(domain).split(".")[0] in _MARKETPLACE_NAMES


# Suffixes where the registrable domain has three labels (shop.co.uk).
_TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "co.in", "net.in", "org.in", "com.au", "co.nz", "co.jp",
    "com.br", "com.mx", "co.za", "com.sg", "com.tr", "co.kr", "com.cn",
}


def registrable_domain(url_or_domain: str) -> str:
    """'https://www.shop.example.co.uk/p/1' -> 'example.co.uk'."""
    text = url_or_domain.strip().lower()
    host = urlparse(text if "//" in text else "//" + text).hostname or text
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@dataclass
class PurchaseIntent:
    """What the agent is about to do."""

    merchant_url: str
    item: str
    price: float
    currency: str = "USD"
    country: str = "us"            # Google `gl` code; where the buyer is
    condition: str = "new"         # "new" or "used"
    need_by_days: int | None = None  # set when the purchase is urgent

    @property
    def domain(self) -> str:
        return registrable_domain(self.merchant_url)


@dataclass
class StoreReport:
    """What the outside world says about a store."""

    domain: str
    known: bool = False                 # Google has a store profile for it
    title: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    badge: str | None = None            # e.g. "Top Quality Store"
    return_window_days: int | None = None
    shipping_note: str | None = None    # e.g. "0-5 days"
    shipping_days_max: int | None = None
    poor_sentiment: dict[str, int] = field(default_factory=dict)  # topic -> % poor
    other_ratings: dict[str, str] = field(default_factory=dict)   # name -> "85/100"
    scamadviser: int | None = None
    summary: str | None = None
    sells: str | None = None            # "lease-to-own" when its description says so
    # Only filled in when the store is unknown:
    first_indexed: str | None = None
    independent_mentions: int | None = None
    lookalike_of: str | None = None     # the real brand site this domain imitates
    error: str | None = None            # the check itself failed


@dataclass
class Offer:
    merchant: str
    domain: str
    total_price: float                  # price + delivery
    rating: float | None = None
    reviews: int | None = None
    title: str = ""
    details: list[str] = field(default_factory=list)
    link: str = ""
    condition_hint: str | None = None   # "refurbished", "open box", ...
    third_party: bool = False           # a marketplace seller, not the store itself


@dataclass
class DealReport:
    """What the same product costs elsewhere."""

    matched: bool = False
    confidence: str = "none"            # "high": this store is in the listing at this price; "medium": title match only
    merchant_price: float | None = None  # what Google lists this store's price as
    product_title: str | None = None
    brand: str | None = None
    typical_low: float | None = None
    typical_high: float | None = None
    median_price: float | None = None   # median of the offers we saw
    offers: list[Offer] = field(default_factory=list)
    new_sellers: list[str] = field(default_factory=list)  # sellers Google lists for this item when filtered to "new"
    error: str | None = None

    def sells_it_new(self, merchant: str) -> bool:
        return seller_key(merchant) in self.new_sellers


def seller_key(name: str) -> str:
    """'Walmart - Focus Camera' -> 'focuscamera'; 'BuyDig.com' -> 'buydigcom'."""
    return "".join(c for c in name.split(" - ")[-1].lower() if c.isalnum())


@dataclass
class Alternative:
    offer: Offer
    saving: float
    saving_pct: float


@dataclass
class Verdict:
    decision: str
    reasons: list[str] = field(default_factory=list)   # why, in plain words
    notes: list[str] = field(default_factory=list)     # things checked that did not change the outcome
    alternative: Alternative | None = None


@dataclass
class Policy:
    """Thresholds. These are opinions; change them."""

    # Proportionality: you don't open five tabs for a $3 cable.
    skip_below: float = 50.0        # known store and cheaper than this: no pause
    full_above: float = 100.0       # at or above this (or any new store): trust + deal check
    known_merchants: set[str] = field(default_factory=set)  # domains the buyer has used before

    # Trust.
    min_rating: float = 3.5
    min_reviews_for_rating: int = 50   # below this, a rating is too thin to judge on its own
    established_years: float = 5.0     # ...unless the domain has been around this long
    established_scamadviser: int = 80  # ...and ScamAdviser scores it at least this
    scamadviser_block_below: int = 30
    short_return_days: int = 14
    returns_matter_above: float = 100.0
    poor_shipping_pct: int = 40

    # Deal.
    min_saving_pct: float = 10.0
    min_saving_abs: float = 5.0
    own_listing_gap_pct: float = 20.0  # agent's price vs the same store's price on Google
    alt_min_rating: float = 4.0
    alt_min_reviews: int = 100
    too_cheap_ratio: float = 0.75      # below 75% of the usual price: probably not the same thing
    scam_cheap_ratio: float = 0.60     # this cheap AND an unknown store: block

    token_ttl_seconds: int = 600

    # Who makes the judgment calls: "rules" (the thresholds above) or "llm"
    # (a model, guided by `preferences`; the thresholds become its fallback).
    # Either way the hard stops in rubric.hard_stops are decided by code.
    judge: str = "rules"
    preferences: str = (
        "Buy from stores a careful person would trust. Only suggest a cheaper seller if the "
        "saving is real money for this purchase and that seller is at least as trustworthy. "
        "I care about being able to return expensive items. If something important is "
        "unknown, ask me instead of guessing."
    )
    llm_min_saving_pct: float = 3.0    # cheaper sellers below this aren't even shown to the model

    def scaled(self, factor: float) -> "Policy":
        """Same policy with money thresholds multiplied (e.g. 85 for INR)."""
        import copy

        policy = copy.copy(self)
        for name in ("skip_below", "full_above", "returns_matter_above", "min_saving_abs"):
            setattr(policy, name, getattr(self, name) * factor)
        return policy
