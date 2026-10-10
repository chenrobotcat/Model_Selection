#!/usr/bin/env python3
"""Summarize model-router decisions. Usage: python3 route_stats.py [days=7]"""
import json
import os
import sys
import time
from collections import Counter

LOG_PATH = os.path.expanduser("~/.claude/router/routes.jsonl")


def main():
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 7
    cutoff = time.time() - days * 86400
    rows = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("ts", 0) >= cutoff:
                    rows.append(r)

    print(f"Last {days:g} days: {len(rows)} Agent calls")
    if not rows:
        return

    def show(title, counter):
        total = sum(counter.values())
        print(f"\n{title}")
        for k, n in counter.most_common():
            print(f"  {k:<10} {n:>5}  {n / total:6.1%}  {'█' * round(30 * n / total)}")

    show("Action", Counter(r["action"] for r in rows))
    tiers = Counter(r["tier"] for r in rows if "tier" in r)
    if tiers:
        show("Tier (classified)", Counter({t: tiers.get(t, 0) for t in ("small", "mid", "top")}))
    models = Counter(r["model"] for r in rows if r["action"] in ("filled", "explicit"))
    if models:
        show("Model set (filled + explicit)", models)
    errors = Counter(r.get("error", "?").split(":")[0] for r in rows if r["action"] == "error")
    if errors:
        show("Errors", errors)


if __name__ == "__main__":
    main()
