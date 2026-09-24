"""Consistency checks over Plan.md. Run before committing a plan change.

Three kinds of drift, all of which have happened at least once:

- A table named in prose and never defined in §5. Foreign-key checking misses it
  entirely, because nothing references a table that does not exist.
- A cross-reference to a section that is not there.
- A `sed`-style edit whose anchor stopped matching, which fails silently and
  leaves the document describing something it does not contain.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLAN = Path(__file__).resolve().parent.parent / "Plan.md"
# Words that look like table names in prose but are columns, types or prose.
IGNORE = {"information_schema", "pg_extension"}


def main() -> int:
    text = PLAN.read_text()
    defined = set(re.findall(r"CREATE TABLE (\w+)", text))
    problems: list[str] = []

    referenced = set(re.findall(r"REFERENCES (\w+)\(", text))
    if missing := sorted(referenced - defined):
        problems.append(f"foreign keys to undefined tables: {missing}")

    # Backticked snake_case identifiers in prose that look like table names.
    prose = set(re.findall(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+){1,3})`", text))
    columns = set(re.findall(r"^\s{2,}(\w+)\s+\w", text, re.M))
    candidates = {
        name for name in prose
        if name.endswith(("s", "_log")) and name not in columns and name not in IGNORE
    }
    if orphans := sorted(candidates - defined - columns):
        # Only flag ones that read like tables: plural and used with a table verb.
        likely = [
            n for n in orphans
            if re.search(rf"(records?|table|rows? in|into|from) `{n}`", text)
            or re.search(rf"`{n}` (records|holds|carries|is a table)", text)
        ]
        if likely:
            problems.append(f"named in prose, never defined: {likely}")

    for section in sorted({int(x) for x in re.findall(r"§(\d+)(?=[^.\d]|\.\d)", text)}):
        if not re.search(rf"^## {section}\. ", text, re.M):
            problems.append(f"reference to §{section}, which has no section")

    for ref in sorted({x for x in re.findall(r"§(\d+\.\d+)", text)}):
        if not re.search(rf"^#{{3,4}} {re.escape(ref)}[ .]", text, re.M):
            problems.append(f"reference to §{ref}, which has no subsection")

    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(f"{len(defined)} tables defined, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
