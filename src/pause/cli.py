"""Command line: pause check | approve | pay | scorecard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .gate import PauseRequired, Receipt, approve, home, load_receipt, pause, pay
from .models import Policy, PurchaseIntent
from .searchapi import SearchApi

_LABELS = {
    "proceed": "PROCEED",
    "suggest_switch": "SUGGEST SWITCH",
    "ask_human": "ASK THE HUMAN",
    "block": "BLOCK",
}


def render(receipt: Receipt) -> str:
    intent, verdict = receipt.intent, receipt.verdict
    lines = [
        f"PAUSE   {intent.domain} | {intent.item} | {intent.price:,.2f} {intent.currency}",
        f"VERDICT {_LABELS[verdict.decision]}",
        "",
        "Why:",
        *[f"  - {reason}" for reason in verdict.reasons],
    ]
    if verdict.notes:
        lines += ["", "Also checked:", *[f"  - {note}" for note in verdict.notes]]
    engines = ", ".join(call["engine"] for call in receipt.searches) or "none"
    judge = receipt.judge
    decided_by = {"rules": "fixed thresholds", "floor": "hard stop (the model was not asked)",
                  "llm": f"LLM judge ({judge.get('model')})"}[judge["mode"]]
    lines += [
        "",
        f"Decided by: {decided_by}",
        f"Searches: {len(receipt.searches)} ({engines}) in {receipt.seconds}s",
        f"Receipt:  {home() / 'receipts' / (receipt.id + '.json')}",
    ]
    if receipt.token:
        lines.append(f"Token:    {receipt.token}")
    elif verdict.alternative:
        lines += [f"To buy from the suggested seller: pause approve {receipt.id} --alternative",
                  f"To buy here anyway:               pause approve {receipt.id}"]
    elif verdict.decision != "block":
        lines.append(f"To go ahead anyway: pause approve {receipt.id}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pause", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="pause before a purchase")
    check.add_argument("--url", required=True, help="store URL or domain the agent is about to pay")
    check.add_argument("--item", required=True)
    check.add_argument("--price", required=True, type=float)
    check.add_argument("--currency", default="USD")
    check.add_argument("--country", default="us", help="buyer's country code (Google gl)")
    check.add_argument("--need-by-days", type=int, help="set if the purchase is urgent")
    check.add_argument("--known", action="append", default=[], metavar="DOMAIN",
                       help="a store you've bought from before (repeatable)")
    check.add_argument("--cache", type=Path, help="cache SearchApi responses in this folder")
    check.add_argument("--json", action="store_true", help="print the full receipt as JSON")
    _judge_options(check)

    ok = commands.add_parser("approve", help="a human approves a flagged purchase")
    ok.add_argument("receipt_id")
    ok.add_argument("--alternative", action="store_true", help="approve the suggested seller instead")

    payment = commands.add_parser("pay", help="mock payment; refuses without a pause token")
    payment.add_argument("--url", required=True)
    payment.add_argument("--item", required=True)
    payment.add_argument("--price", required=True, type=float)
    payment.add_argument("--currency", default="USD")
    payment.add_argument("--token")

    score = commands.add_parser("scorecard", help="run a file of test cases")
    score.add_argument("cases", type=Path, nargs="?", default=Path("cases/cases.json"))
    score.add_argument("--cache", type=Path, help="cache SearchApi responses in this folder")
    score.add_argument("--out", type=Path, help="also write the table to this markdown file")
    _judge_options(score)

    args = parser.parse_args(argv)

    if args.command == "check":
        intent = PurchaseIntent(args.url, args.item, args.price, args.currency.upper(),
                                args.country, need_by_days=args.need_by_days)
        receipt = pause(intent, SearchApi(cache_dir=args.cache),
                        _policy(args, known_merchants=set(args.known)))
        print(receipt.to_json() if args.json else render(receipt))
        return 0 if receipt.verdict.decision == "proceed" else 2

    if args.command == "approve":
        try:
            token = approve(load_receipt(args.receipt_id),
                            "alternative" if args.alternative else "original")
        except (PauseRequired, FileNotFoundError, ValueError) as exc:
            print(f"Not approved: {exc}", file=sys.stderr)
            return 1
        print(token)
        return 0

    if args.command == "pay":
        try:
            print(pay(args.url, args.item, args.price, args.currency.upper(), args.token))
        except PauseRequired as exc:
            print(f"Refused: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "scorecard":
        from .scorecard import run

        return run(args.cases, args.cache, args.out, _policy(args))
    return 1


def _judge_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--judge", choices=["rules", "llm"], default="rules",
                        help="who makes the judgment calls: fixed thresholds, or a model")
    parser.add_argument("--preferences", help="for --judge llm: what you care about, in a sentence")


def _policy(args: argparse.Namespace, **fields) -> Policy:
    policy = Policy(judge=args.judge, **fields)
    if args.preferences:
        policy.preferences = args.preferences
    return policy


if __name__ == "__main__":
    raise SystemExit(main())
