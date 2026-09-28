"""Pin Dockerfile base image tags to sha256 digests (tags move; §16.1 reproducibility).

Plan.md §2.2.

    python scripts/pin_digests.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FROM = re.compile(r"^(FROM\s+)([^\s@]+?)(?::([^\s@]+))?(\s+AS\s+\S+)?\s*$", re.M)


def resolve(reference: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["docker", "buildx", "imagetools", "inspect", reference, "--format",
             "{{json .Manifest.Digest}}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.strip().strip('"') or None


STAGE = re.compile(r"^FROM\s+\S+\s+AS\s+(\S+)", re.M | re.I)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    changed = 0
    for dockerfile in sorted((root / "docker").glob("*.Dockerfile")):
        text = dockerfile.read_text()
        # Names defined by earlier stages are local, not registry references.
        stages = {name.lower() for name in STAGE.findall(text)}

        def substitute(match: re.Match[str], stages: set[str] = stages) -> str:
            nonlocal changed
            prefix, image, tag, alias = match.groups()
            if "@sha256:" in match.group(0) or image.lower() in stages:
                return match.group(0)
            reference = f"{image}:{tag}" if tag else image
            digest = resolve(reference)
            if digest is None:
                print(f"  ! could not resolve {reference}", file=sys.stderr)
                return match.group(0)
            changed += 1
            print(f"  {reference} -> {digest[:19]}...")
            return f"{prefix}{image}:{tag or 'latest'}@{digest}{alias or ''}"

        updated = FROM.sub(substitute, text)
        if updated != text:
            dockerfile.write_text(updated)
    print(f"\npinned {changed} base images")
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
