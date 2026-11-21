"""Turn a decision into the two-to-four sentence reason and the evidence ids that exist."""

import re

BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z£])")


def render(case, decision):
    parts = _sentences(decision.get("sentences") or [])
    if len(parts) < 2:
        parts.append("The documents and payments named above are what decide this case.")
    parts = parts[:4]
    known = case["known_ids"]
    evidence = []
    seen = set()
    for item in decision.get("evidence") or []:
        if item in known and item not in seen:
            seen.add(item)
            evidence.append(item)
    evidence.sort()
    if not evidence:
        for alert in case["alerts"]:
            for txn in case["flagged"].get(alert["alert_id"], []):
                evidence.append(txn["transaction_id"])
                break
            if evidence:
                break
    return {
        "case_id": case["case"]["case_id"],
        "alert_ids": list(case["alert_ids"]),
        "verdict": decision["verdict"],
        "reason_code": decision["reason_code"],
        "reason": " ".join(parts),
        "evidence": evidence,
    }


def _sentences(raw):
    text = " ".join(sentence.strip() for sentence in raw if sentence and sentence.strip())
    parts = [part.strip() for part in BOUNDARY.split(text) if part.strip()]
    cleaned = []
    for part in parts:
        if part[-1] not in ".!?":
            part += "."
        cleaned.append(part)
    return cleaned
