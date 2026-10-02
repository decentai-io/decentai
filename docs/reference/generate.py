"""Write docs/reference/actions.md from the action catalog.

The catalog (backend/server/authentication/catalog.py) is the one place
an action exists at all, so this page is generated from it rather than
kept by hand: every gateway action, the sentence that describes it,
whether every member holds it by default, and whether the runtime's
delegation may call it.

    python docs/reference/generate.py
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TOKEN_SECRET_KEY", "generate")

from server.authentication.catalog import (  # noqa: E402
    ACTION_CATALOG, BASELINE_ACTIONS, RUNTIME_ENDPOINTS,
)


def runtime_may(action: str) -> bool:
    return any(fnmatch.fnmatchcase(action, pattern) for pattern in RUNTIME_ENDPOINTS)


def main() -> int:
    lines = [
        "# Actions",
        "",
        "Every operation the gateway dispatches, by service. An action is an",
        "endpoint's name — `POST /app` with `endpoint: \"Domain:Controller:action\"` —",
        "and the name is what a policy grants. **Baseline** actions are held by",
        "every member of an organization without a decision; **runtime** marks",
        "the actions a chat's delegation may call, and nothing else.",
        "",
        "Generated from `backend/server/authentication/catalog.py` by",
        "`docs/reference/generate.py`; do not edit by hand.",
        "",
        f"{sum(len(s['actions']) for s in ACTION_CATALOG.values())} actions in "
        f"{len(ACTION_CATALOG)} services; {len(BASELINE_ACTIONS)} baseline.",
        "",
    ]
    baseline = set(BASELINE_ACTIONS)
    for key, service in ACTION_CATALOG.items():
        lines.append(f"## {service.get('label') or key}")
        lines.append("")
        lines.append("| Action | Grants | Baseline | Runtime |")
        lines.append("|---|---|---|---|")
        for action, text in service["actions"].items():
            words = " ".join(str(text).split())
            lines.append(f"| `{action}` | {words} | "
                         f"{'yes' if action in baseline else ''} | "
                         f"{'yes' if runtime_may(action) else ''} |")
        lines.append("")
    out = Path(__file__).resolve().parent / "actions.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
