"""Fail if the README quotes a rupee figure the report does not contain.

The README says "nothing in this README is hand-typed". That is a claim about
a file a human edits, so it needs enforcing rather than trusting: a number
copied in during a rewrite and then left behind by the next evaluation run is
the most ordinary way for a project to end up publishing something false.

Deliberately narrow. It checks that every rupee amount in the README's results
table appears somewhere in ``results/report.md``. It does not check prose, and
it cannot tell you the number means what the surrounding sentence claims.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

AMOUNT = re.compile(r"₹[\d,]*\d(?:\.\d+)?")
"""Must not swallow a trailing comma: "₹14,27,696," is not a figure the
report contains, and matching it produced a false failure on the first run."""


def main() -> int:
    readme = Path("README.md").read_text(encoding="utf-8")
    report = Path("results/report.md").read_text(encoding="utf-8")

    table = readme.split("## Results", 1)
    if len(table) < 2:
        print("README has no Results section to check", file=sys.stderr)
        return 0
    section = table[1].split("\n## ", 1)[0]

    missing = []
    for raw in AMOUNT.findall(section):
        # The README rounds to whole rupees where the report carries paise.
        stem = raw.split(".")[0]
        if stem not in report:
            missing.append(raw)

    if missing:
        print("These figures appear in the README but not in results/report.md:",
              file=sys.stderr)
        for item in missing:
            print(f"  {item}", file=sys.stderr)
        print("\nRegenerate the report, or correct the README. A hand-typed number "
              "that no longer matches is how a project publishes something false.",
              file=sys.stderr)
        return 1

    print(f"all {len(AMOUNT.findall(section))} figures in the README appear in the report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
