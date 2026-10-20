"""Check every operator's sources once and write a shields.io endpoint badge per operator.

    uv run python scripts/source_status.py --out status/

Writes ``<operator>.json`` (https://shields.io/badges/endpoint-badge), ``summary.json`` and a
Markdown table (``ua_outages.status``); exits with 1 when an operator that should work has no
working source. On GitHub's runners the sites that answer only Ukrainian connections are not
queried. The published badges come from the Telegram bot, which checks from Ukraine.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from ua_outages import now_kyiv
from ua_outages.http import new_session
from ua_outages.status import OperatorStatus, cell, check_all, files, table

__all__ = ["cell", "main"]


async def check(*, abroad: bool) -> list[OperatorStatus]:
    async with new_session() as session:
        return await check_all(session, abroad=abroad)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path("status"))
    args = parser.parse_args()
    # GitHub's own runners are abroad; a self-hosted runner in Ukraine sets UKRAINE_NETWORK=1.
    abroad = os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("UKRAINE_NETWORK") != "1"
    statuses = asyncio.run(check(abroad=abroad))
    args.out.mkdir(parents=True, exist_ok=True)
    for name, content in files(statuses, now_kyiv()).items():
        (args.out / name).write_text(content, encoding="utf-8")
    markdown = table(statuses)
    print(markdown)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(path).write_text("### Джерела графіків\n\n" + markdown + "\n", encoding="utf-8")
    sys.exit(1 if any(s.status.broken for s in statuses) else 0)


if __name__ == "__main__":
    main()
