"""Run a file of purchase scenarios through the pause and print a table.

Each case is a purchase an agent might be about to make, plus the verdict a
careful human would expect. Use it to see how the rubric behaves before you
trust it, and to re-check after changing a threshold.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from .gate import pause
from .models import Policy, PurchaseIntent
from .searchapi import SearchApi, SearchApiError

_LABELS = {"proceed": "Proceed", "suggest_switch": "Suggest switch",
           "ask_human": "Ask the human", "block": "Block"}


class _Offline(SearchApi):
    """Stands in for SearchApi being unreachable."""

    def search(self, engine: str, **params) -> dict:
        raise SearchApiError(f"{engine}: network unreachable (simulated)")


def run(cases_file: Path, cache_dir: Path | None = None, out: Path | None = None) -> int:
    cases = json.loads(Path(cases_file).read_text())
    rows, failures, timings = [], 0, []
    for number, case in enumerate(cases, start=1):
        policy = Policy(known_merchants=set(case.get("known_merchants", [])))
        if case.get("threshold_scale"):
            policy = policy.scaled(case["threshold_scale"])
        api = _Offline() if case.get("simulate_outage") else SearchApi(cache_dir=cache_dir)
        receipt = pause(PurchaseIntent(**case["intent"]), api, policy)

        got, expected = receipt.verdict.decision, case["expect"]
        passed = got == expected
        failures += not passed
        live = [call for call in receipt.searches if not call["cached"]]
        seconds = f"{receipt.seconds:.1f}s" if live or not receipt.searches else "cached"
        if live:
            timings.append(receipt.seconds)
        why = receipt.verdict.reasons[0] if receipt.verdict.reasons else ""
        rows.append((number, case["name"], _LABELS[got], "yes" if passed else f"NO (expected {_LABELS[expected]})",
                     why, len(receipt.searches), seconds))
        print(f"[{number:>2}/{len(cases)}] {'ok ' if passed else 'FAIL'} {case['name']}: {_LABELS[got]}", flush=True)

    header = "| # | Case | Verdict | As expected | Why (first reason) | Searches | Time |"
    table = "\n".join(
        [header, "|---|---|---|---|---|---|---|"]
        + [f"| {n} | {name} | **{verdict}** | {ok} | {why} | {searches} | {seconds} |"
           for n, name, verdict, ok, why, searches, seconds in rows]
    )
    total = sum(row[5] for row in rows)
    summary = (f"\n{len(rows) - failures}/{len(rows)} as expected. {total} searches in total, "
               f"{total / len(rows):.1f} per pause on average.")
    if timings:
        summary += (f" Pauses that searched live took {median(timings):.1f}s (median), "
                    f"{max(timings):.1f}s at worst.")
    print("\n" + table + "\n" + summary)
    if out:
        Path(out).write_text(table + "\n" + summary + "\n")
    return 1 if failures else 0
