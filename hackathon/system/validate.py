#!/usr/bin/env python3
"""Check an output file against one institution folder.

Usage: python3 validate.py <institution_folder> <output.jsonl>
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CODES_FOR
from explain import BOUNDARY
from loader import load_institution


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 validate.py <institution_folder> <output.jsonl>\n")
        return 2
    bundle = load_institution(os.path.abspath(argv[1]))
    known = set(bundle["txns_by_id"]) | set(bundle["list_by_id"])
    for rows in bundle["evidence_by_customer"].values():
        for row in rows:
            known.add(row["doc_id"])
    expected = [case["case_id"] for case in bundle["cases"]]
    seen = []
    problems = []
    with open(os.path.abspath(argv[2]), encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append("line %d is not json: %s" % (line_no, exc))
                continue
            seen.append(row.get("case_id"))
            verdict = row.get("verdict")
            code = row.get("reason_code")
            if verdict not in CODES_FOR:
                problems.append("%s verdict %r" % (row.get("case_id"), verdict))
            elif code not in CODES_FOR[verdict]:
                problems.append("%s code %s does not belong to %s" % (row.get("case_id"), code, verdict))
            reason = row.get("reason") or ""
            count = len([part for part in BOUNDARY.split(reason) if part.strip()])
            if count < 2 or count > 4:
                problems.append("%s has %d sentences" % (row.get("case_id"), count))
            if not isinstance(row.get("alert_ids"), list) or not row["alert_ids"]:
                problems.append("%s missing alert_ids" % row.get("case_id"))
            for item in row.get("evidence") or []:
                if item not in known:
                    problems.append("%s cites unknown id %s" % (row.get("case_id"), item))
            if not row.get("evidence"):
                problems.append("%s has no evidence" % row.get("case_id"))
    missing = [case_id for case_id in expected if case_id not in set(seen)]
    extra = [case_id for case_id in seen if case_id not in set(expected)]
    duplicates = len(seen) - len(set(seen))
    if missing:
        problems.append("missing %d cases, first %s" % (len(missing), missing[:5]))
    if extra:
        problems.append("unexpected %d cases, first %s" % (len(extra), extra[:5]))
    if duplicates:
        problems.append("%d duplicate case ids" % duplicates)
    if seen != expected:
        problems.append("output order differs from cases.jsonl")
    sys.stdout.write(
        "cases=%d problems=%d\n" % (len(expected), len(problems))
    )
    for problem in problems[:40]:
        sys.stdout.write(problem + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
