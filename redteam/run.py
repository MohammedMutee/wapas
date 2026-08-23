"""Run the adversarial suite. Expect zero escapes.

    python -m redteam.run

Exits non-zero if anything got through, so CI treats an escape as a failed
build rather than a line of output nobody reads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redteam.injection import INJECTION_SCENARIOS
from redteam.scenarios import SCENARIOS
from redteam.tamper import TAMPER_SCENARIOS


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="results/redteam.md")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    groups = [
        ("Policy gate", SCENARIOS),
        ("Prompt injection", INJECTION_SCENARIOS),
        ("Evidence tampering", TAMPER_SCENARIOS),
    ]

    escapes = []
    lines: list[str] = []
    A = lines.append
    A("# Wapas — adversarial suite")
    A("")
    total = sum(len(s) for _, s in groups)
    A(f"{total} scenarios. Each one tries to make the system do something it must")
    A("not. An **escape** is a scenario that succeeded, and any escape is a failed")
    A("build.")
    A("")
    A("Unit tests check that each rule works. This checks that someone who knows the")
    A("design cannot get around them — including through paths no single rule owns: a")
    A("misdiagnosis unlocking the wrong playbook, counterparty free text reaching a")
    A("model, an audit chain edited after the fact.")
    A("")

    for title, scenarios in groups:
        A(f"## {title}")
        A("")
        A("| | Scenario | The attack | Result |")
        A("|---|---|---|---|")
        for sc in scenarios:
            try:
                contained, evidence = sc.check()
            except Exception as exc:  # a crash is not containment
                contained, evidence = False, f"raised {type(exc).__name__}: {exc}"
            mark = "✅" if contained else "🚨"
            if not contained:
                escapes.append((sc, evidence))
            A(f"| {mark} | `{sc.id}` | {sc.attack} | {evidence} |")
        A("")

    A("## Verdict")
    A("")
    if escapes:
        A(f"**{len(escapes)} ESCAPES.** This build must not ship.")
        A("")
        for sc, evidence in escapes:
            A(f"### 🚨 `{sc.id}`")
            A("")
            A(f"- **Attack:** {sc.attack}")
            A(f"- **Required:** {sc.must}")
            A(f"- **Got:** {evidence}")
            A(f"- **Why it matters:** {sc.stakes}")
            A("")
    else:
        A(f"**0 escapes across {total} scenarios.**")
        A("")
        A("Worth being precise about what that does and does not mean. It means every")
        A("attack written down here was contained. It does not mean the system is safe,")
        A("because the suite only contains attacks somebody thought of — and the")
        A("interesting failures in this project so far have all been ones nobody thought")
        A("of until the numbers looked wrong.")
    A("")
    A("Reproduce: `make redteam`")

    report = "\n".join(lines) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    if not args.quiet:
        print(report)
    print(f"\nwrote {out} — {len(escapes)} escapes of {total}", file=sys.stderr)
    return 1 if escapes else 0


if __name__ == "__main__":
    raise SystemExit(main())
