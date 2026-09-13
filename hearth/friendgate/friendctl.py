"""friendctl: mint, revoke, list friend keys; read usage.

    python -m hearth.friendgate.friendctl mint --account klaus [--display "Klaus"] [--models qwen38-27b-mtp]
                                               [--max-context 65536] [--tokens-per-day 3000000] [--expires-days 30]
    python -m hearth.friendgate.friendctl list
    python -m hearth.friendgate.friendctl revoke <key_id>
    python -m hearth.friendgate.friendctl usage [--account klaus]

The plaintext key is printed exactly once by `mint`; only its sha256 is stored. The gate reloads the
keys file on change, so no restart is needed.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from .app import DEFAULT_VAR
from .keys import KeyStore
from .usage import UsageLog


def main(argv: list[str] | None = None) -> int:
    var = Path(os.environ.get("FRIENDGATE_VAR", str(DEFAULT_VAR)))
    ap = argparse.ArgumentParser(prog="friendctl", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("--account", required=True, help="the friend's IRC (Ergo) account")
    m.add_argument("--display", default="")
    m.add_argument("--models", nargs="*", default=[], help="allow-list; empty = any eligible model")
    m.add_argument("--max-concurrent", type=int, default=1)
    m.add_argument("--max-context", type=int, default=65_536)
    m.add_argument("--tokens-per-day", type=int, default=3_000_000)
    m.add_argument("--expires-days", type=float, default=None)
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("key_id")
    u = sub.add_parser("usage")
    u.add_argument("--account", default=None)
    a = ap.parse_args(argv)

    store = KeyStore(var / "keys.json")
    if a.cmd == "mint":
        rec, secret = store.mint(a.account, a.display, a.models, a.max_concurrent, a.max_context, a.tokens_per_day, a.expires_days)
        print(f"key_id: {rec.key_id}   account: {rec.irc_account}   max_context: {rec.max_context_tokens}   tokens/day: {rec.tokens_per_day:,}")
        print("give this to the friend once (it is not stored):")
        print(secret)
        return 0
    if a.cmd == "list":
        for k in store.all():
            exp = time.strftime("%Y-%m-%d", time.localtime(k.expires)) if k.expires else "-"
            print(f"{k.key_id:<28} {k.irc_account:<16} {k.status():<8} ctx={k.max_context_tokens:<7} day={k.tokens_per_day:<10,} models={','.join(k.models) or 'any':<20} expires={exp}")
        return 0
    if a.cmd == "revoke":
        ok = store.revoke(a.key_id)
        print("revoked" if ok else "no such key_id")
        return 0 if ok else 1
    if a.cmd == "usage":
        rows = UsageLog(var / "usage.ndjson").summary(a.account)
        if not rows:
            print("no usage yet")
            return 0
        print(f"{'day':<11} {'key_id':<28} {'account':<14} {'calls':>5} {'ok':>4} {'tokens_in':>10} {'tokens_out':>10} {'seconds':>8}")
        for t in rows:
            print(f"{t['day']:<11} {t['key_id']:<28} {t['irc_account']:<14} {t['calls']:>5} {t['ok']:>4} {t['tokens_in']:>10,} {t['tokens_out']:>10,} {t['seconds']:>8.0f}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
