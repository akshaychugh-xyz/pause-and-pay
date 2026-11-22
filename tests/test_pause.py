"""End-to-end tests of pause() against made-up SearchApi data. No network."""

from __future__ import annotations

import pytest

from fakes import ITEM, FakeApi
from pause import (ASK_HUMAN, BLOCK, PROCEED, SUGGEST_SWITCH, PauseRequired, Policy,
                   PurchaseIntent, approve, load_receipt, pause, pay)
from pause.models import registrable_domain
from pause.trust import domain_age_years


@pytest.fixture(autouse=True)
def pause_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PAUSE_HOME", str(tmp_path))
    monkeypatch.setenv("PAUSE_SECRET", "test-secret")


def run(domain, price, item=ITEM, policy=None, api=None, **intent):
    return pause(PurchaseIntent(f"https://www.{domain}/checkout", item, price, **intent),
                 api or FakeApi(), policy)


def engines(receipt):
    return [call["engine"] for call in receipt.searches]


# ------------------------------------------------------------------ verdicts

def test_good_store_fair_price_proceeds_with_a_token():
    receipt = run("bigbox.test", 299.99)
    assert receipt.verdict.decision == PROCEED
    assert receipt.token
    assert receipt.deal.confidence == "high"
    assert len(receipt.searches) == 4  # store, shopping, product, offers


def test_pricier_store_suggests_the_trusted_cheaper_seller():
    receipt = run("pricey.test", 399.99)
    assert receipt.verdict.decision == SUGGEST_SWITCH
    assert receipt.token is None
    alternative = receipt.verdict.alternative
    assert alternative.offer.domain == "bigbox.test"
    assert alternative.saving == 100.00


def test_cheapest_is_not_best():
    """Every cheaper offer is passed over for a stated reason."""
    notes = " ".join(run("pricey.test", 399.99).verdict.notes)
    assert "Marketplace - somebody" in notes and "marketplace listing" in notes
    assert "LeaseCo" in notes and "can't confirm it sells this item new" in notes
    assert "LowRated Camera" in notes and "open box" in notes
    assert "Refurb Depot" in notes  # well rated, but not known to sell it new


def test_low_rated_store_asks_the_human():
    receipt = run("lowrated.test", 299.99)
    assert receipt.verdict.decision == ASK_HUMAN
    assert "rated 2.7/5 from 461 reviews" in receipt.verdict.reasons[0]


def test_lease_to_own_store_asks_the_human():
    receipt = run("lease.test", 244.94)
    assert receipt.verdict.decision == ASK_HUMAN
    assert any("lease-to-own" in reason for reason in receipt.verdict.reasons)


def test_unknown_store_asks_the_human():
    receipt = run("nobody-knows-us.test", 289.00)
    assert receipt.verdict.decision == ASK_HUMAN
    assert "no store profile" in receipt.verdict.reasons[0]
    assert "google_about_this_domain" in engines(receipt)


def test_unknown_store_with_a_too_good_price_is_blocked():
    receipt = run("nobody-knows-us.test", 119.00)
    assert receipt.verdict.decision == BLOCK
    assert "below the usual" in receipt.verdict.reasons[0]


def test_lookalike_domain_is_blocked():
    receipt = run("acme-outlet-deals.test", 279.00)
    assert receipt.verdict.decision == BLOCK
    assert "the brand's own site is acme.test" in receipt.verdict.reasons[0]


def test_price_far_below_usual_asks_the_human_even_at_a_good_store():
    receipt = run("goodalt.test", 199.00)
    assert receipt.verdict.decision == ASK_HUMAN
    assert "used, refurbished or a different variant" in " ".join(receipt.verdict.reasons)


def test_few_reviews_but_long_established_domain_proceeds():
    receipt = run("tinybrand.test", 60.00, item="Tiny Brand Canvas Tote")
    assert receipt.verdict.decision == PROCEED
    assert "known for 10+ years" in " ".join(receipt.verdict.reasons)


def test_short_return_window_on_an_expensive_item():
    receipt = run("shortreturns.test", 299.99)
    assert receipt.verdict.decision == ASK_HUMAN
    assert "Return window is only 7 days" in receipt.verdict.reasons[0]


def test_urgent_purchase_at_a_slow_shipper():
    assert run("slowship.test", 299.99).verdict.decision == PROCEED
    receipt = run("slowship.test", 299.99, need_by_days=2)
    assert receipt.verdict.decision == ASK_HUMAN
    assert "ships in up to 6" in receipt.verdict.reasons[0]


def test_outage_means_ask_never_proceed():
    receipt = run("bigbox.test", 299.99, api=FakeApi(down=True))
    assert receipt.verdict.decision == ASK_HUMAN
    assert "Unknown is not the same as safe" in receipt.verdict.reasons[0]
    assert receipt.token is None


# ----------------------------------------------------------- proportionality

def test_small_purchase_at_a_known_store_skips_the_pause():
    policy = Policy(known_merchants={"bigbox.test"})
    receipt = run("bigbox.test", 7.99, item="AA Batteries 8 Pack", policy=policy)
    assert (receipt.tier, receipt.verdict.decision, receipt.searches) == ("skip", PROCEED, [])
    assert receipt.token


def test_mid_purchase_at_a_known_store_checks_trust_only():
    policy = Policy(known_merchants={"bigbox.test"})
    receipt = run("bigbox.test", 74.99, item="Game Controller", policy=policy)
    assert receipt.tier == "trust"
    assert engines(receipt) == ["google_about_this_store"]


def test_small_purchase_at_a_new_store_still_gets_the_full_check():
    assert run("bigbox.test", 7.99).tier == "full"


def test_a_store_you_have_used_before_gets_the_benefit_of_the_doubt():
    policy = Policy(known_merchants={"lowrated.test"})
    assert run("lowrated.test", 299.99, policy=policy).verdict.decision == PROCEED


# ------------------------------------------------------------------ the lock

def test_pay_refuses_without_a_token():
    with pytest.raises(PauseRequired, match="No pause token"):
        pay("https://www.bigbox.test", ITEM, 299.99)


def test_token_is_bound_to_store_item_and_amount():
    token = run("bigbox.test", 299.99).token
    assert pay("https://bigbox.test/checkout", ITEM, 299.99, "USD", token)["status"] == "paid (mock)"
    for url, item, price in [("https://www.bigbox.test", ITEM, 349.99),
                             ("https://www.pricey.test", ITEM, 299.99),
                             ("https://www.bigbox.test", "Something else", 299.99)]:
        with pytest.raises(PauseRequired, match="different store, item or amount"):
            pay(url, item, price, "USD", token)


def test_token_expires():
    token = run("bigbox.test", 299.99, policy=Policy(token_ttl_seconds=-1)).token
    with pytest.raises(PauseRequired, match="expired"):
        pay("https://www.bigbox.test", ITEM, 299.99, "USD", token)


def test_tampered_token_is_rejected():
    token = run("bigbox.test", 299.99).token
    with pytest.raises(PauseRequired, match="not valid"):
        pay("https://www.bigbox.test", ITEM, 299.99, "USD", token[:-1] + ("0" if token[-1] != "0" else "1"))


def test_human_can_approve_the_alternative_but_not_a_block():
    flagged = load_receipt(run("pricey.test", 399.99).id)  # round-trips through the receipt file
    token = approve(flagged, "alternative")
    assert pay("https://www.bigbox.test", ITEM, 299.99, "USD", token)["merchant"] == "bigbox.test"
    with pytest.raises(PauseRequired):
        pay("https://www.pricey.test", ITEM, 399.99, "USD", token)

    blocked = run("acme-outlet-deals.test", 279.00)
    with pytest.raises(PauseRequired, match="blocked"):
        approve(blocked)


# ------------------------------------------------------------------- helpers

def test_registrable_domain():
    assert registrable_domain("https://www.shop.example.co.uk/p/1?x=2") == "example.co.uk"
    assert registrable_domain("electronics.sony.com") == "sony.com"
    assert registrable_domain("HTTPS://BestBuy.com") == "bestbuy.com"


def test_domain_age_years():
    from datetime import date

    today = date(2026, 11, 15)
    assert domain_age_years("x.com was indexed by Google more than 10 years ago", today) == 10.0
    assert domain_age_years("x.com was first indexed by Google in October 2019", today) == 7.1
    assert domain_age_years(None) is None


# ------------------------------------------------------------ a rejected key

def _reject_key(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{\n  "error": "Invalid API key."\n}'))
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def test_rejected_key_is_one_clear_line(monkeypatch):
    from pause import SearchApi, SearchApiError

    _reject_key(monkeypatch)
    with pytest.raises(SearchApiError) as error:
        SearchApi(api_key="wrong").search("google_about_this_store", domain="bigbox.test")
    assert "Check SEARCHAPI_API_KEY" in str(error.value) and "\n" not in str(error.value)


def test_scorecard_stops_at_a_rejected_key(monkeypatch, tmp_path, capsys):
    import json

    from pause.scorecard import run as run_scorecard

    _reject_key(monkeypatch)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "wrong")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"name": "one", "intent": {"merchant_url": "https://bigbox.test", "item": ITEM, "price": 299.99}, "expect": "proceed"},
        {"name": "two", "intent": {"merchant_url": "https://pricey.test", "item": ITEM, "price": 399.99}, "expect": "proceed"},
    ]))
    assert run_scorecard(cases) == 2
    output = capsys.readouterr().out
    assert "Check SEARCHAPI_API_KEY" in output and "two" not in output


# ------------------------------------------------------------ thin evidence

def test_one_seller_is_not_a_usual_price(monkeypatch):
    """A listing with a single seller must not produce 'price is in line' or 'too cheap'."""
    import fakes

    monkeypatch.setattr(fakes, "PRODUCT", {
        "product": {"title": ITEM, "brand": "Acme"},
        "offers": [fakes.offer(1, "BigBox", "bigbox.test", 398.00)],
    })
    monkeypatch.setattr(fakes, "MORE_OFFERS", {"offers": []})
    receipt = run("bigbox.test", 199.00)
    assert receipt.deal.median_price is None
    assert receipt.verdict.decision == PROCEED
    text = " ".join(receipt.verdict.reasons + receipt.verdict.notes)
    assert "too few to say what the usual price is" in text
    assert "in line" not in text and "below the usual" not in text


def test_lookalike_names_the_site_that_carries_the_brand(monkeypatch):
    import fakes

    original = fakes.FakeApi.search

    def search(self, engine, **params):
        if engine == "google":
            self.calls.append(fakes.Call(engine, "search_x", 0.0))
            return {"organic_results": [{"link": "https://www.gamesbrand.test/"},
                                        {"link": "https://www.acme.test/"}]}
        return original(self, engine, **params)

    monkeypatch.setattr(fakes.FakeApi, "search", search)
    receipt = run("acme-outlet-deals.test", 279.00)
    assert receipt.verdict.decision == BLOCK
    assert "the brand's own site is acme.test" in receipt.verdict.reasons[0]
