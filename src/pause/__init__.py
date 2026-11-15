"""Pay with a pause: a checkpoint an AI agent must pass before it pays."""

from .deal import find_better_deal
from .gate import PauseRequired, Receipt, approve, load_receipt, pause, pay, verify_token
from .models import (ASK_HUMAN, BLOCK, PROCEED, SUGGEST_SWITCH, DealReport, Offer, Policy,
                     PurchaseIntent, StoreReport, Verdict)
from .rubric import decide
from .searchapi import SearchApi, SearchApiError
from .trust import check_store

__all__ = [
    "pause", "pay", "approve", "decide", "check_store", "find_better_deal", "verify_token",
    "load_receipt", "PauseRequired", "Receipt", "PurchaseIntent", "Policy", "StoreReport",
    "DealReport", "Offer", "Verdict", "SearchApi", "SearchApiError",
    "PROCEED", "SUGGEST_SWITCH", "ASK_HUMAN", "BLOCK",
]
