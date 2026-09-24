"""Record a new golden digest. Requires a written reason (Plan.md §16.6)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from tradelab.determinism import check_environment, golden_run

TARGET = Path(__file__).resolve().parent.parent / "tests" / "golden" / "expected.json"


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Refusing: a reason is required. make golden-update REASON='...'")
        return 1
    check_environment(strict=True)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = "uncommitted"
    payload = {
        "digest": golden_run(),
        "reason": sys.argv[1],
        "recorded_at": datetime.now(UTC).isoformat(),
        "code_version": sha,
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Recorded {payload['digest']}\n  reason: {payload['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
