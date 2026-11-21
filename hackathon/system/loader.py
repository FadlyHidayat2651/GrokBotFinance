"""Load one institution folder into memory. Paths are absolute and the process cwd is irrelevant."""

import json
import os


def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_institution(folder):
    folder = os.path.abspath(folder)
    institution = os.path.basename(folder.rstrip(os.sep))
    policy_path = os.path.join(folder, "POLICY.md")
    with open(policy_path, encoding="utf-8") as fh:
        policy_text = fh.read()

    cases = _read_jsonl(os.path.join(folder, "cases.jsonl"))
    alerts = _read_jsonl(os.path.join(folder, "alerts.jsonl"))
    customers = _read_jsonl(os.path.join(folder, "customers.jsonl"))
    transactions = _read_jsonl(os.path.join(folder, "transactions.jsonl"))
    evidence = _read_jsonl(os.path.join(folder, "evidence.jsonl"))
    reviews = _read_jsonl(os.path.join(folder, "previous_reviews.jsonl"))
    internal_list = _read_jsonl(os.path.join(folder, "internal_list.jsonl"))

    alerts_by_id = {row["alert_id"]: row for row in alerts}
    alerts_by_case = {}
    for row in alerts:
        alerts_by_case.setdefault(row["case_id"], []).append(row)
    customers_by_id = {row["customer_id"]: row for row in customers}
    txns_by_customer = {}
    txns_by_id = {}
    for row in transactions:
        txns_by_customer.setdefault(row["customer_id"], []).append(row)
        txns_by_id[row["transaction_id"]] = row
    for rows in txns_by_customer.values():
        rows.sort(key=lambda row: (row.get("booked_at") or "", row["transaction_id"]))
    evidence_by_customer = {}
    for row in evidence:
        evidence_by_customer.setdefault(row["customer_id"], []).append(row)
    for rows in evidence_by_customer.values():
        rows.sort(key=lambda row: (row.get("dated") or "", row["doc_id"]))
    reviews_by_customer = {}
    for row in reviews:
        reviews_by_customer.setdefault(row["customer_id"], []).append(row)
    list_by_id = {row["entry_id"]: row for row in internal_list}

    return {
        "folder": folder,
        "institution": institution,
        "policy_text": policy_text,
        "cases": cases,
        "alerts_by_id": alerts_by_id,
        "alerts_by_case": alerts_by_case,
        "customers_by_id": customers_by_id,
        "txns_by_customer": txns_by_customer,
        "txns_by_id": txns_by_id,
        "evidence_by_customer": evidence_by_customer,
        "reviews_by_customer": reviews_by_customer,
        "list_by_id": list_by_id,
    }
