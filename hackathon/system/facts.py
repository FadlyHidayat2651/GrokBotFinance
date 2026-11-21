"""Deterministic document and case facts. No network, no randomness."""

import os
import re
from datetime import date

MONEY_RE = re.compile(r"£\s*([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)\.(\d{2})")
FIELD_RE = re.compile(r"^\*\*([^*]+):\*\*\s*(.*)$")
ACCOUNT_RE = re.compile(
    r"(\d{2}-\d{2}-\d{2}\s+\d{6,10}|account ending\s+\d{3,8}|IBAN ending\s+\d{3,8})",
    re.IGNORECASE,
)
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
NAME_DROP = {
    "ltd",
    "limited",
    "plc",
    "llp",
    "co",
    "company",
    "the",
    "and",
    "of",
    "client",
    "account",
    "from",
    "pty",
    "inc",
    "for",
}
GOODS_STOP = NAME_DROP | {
    "imports",
    "import",
    "buys",
    "buy",
    "uk",
    "goods",
    "order",
    "shipment",
    "services",
    "service",
    "supplies",
    "supply",
    "commercial",
    "equipment",
    "with",
    "that",
    "this",
    "from",
    "into",
    "paid",
    "payment",
}


def gbp(amount):
    return "£{:,.2f}".format(float(amount))


def norm(value):
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(value):
    return [word for word in norm(value).split() if word not in NAME_DROP and len(word) > 2]


def party_overlap(left, right):
    left_tokens = set(tokens(left))
    right_tokens = set(tokens(right))
    if not left_tokens or not right_tokens:
        return False
    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        return True
    shared = left_tokens & right_tokens
    if any(len(word) >= 5 for word in shared):
        return True
    return len(shared) >= 2


def content_words(value):
    return {word for word in norm(value).split() if len(word) > 3 and word not in GOODS_STOP}


def parse_date(value):
    """Return (date, 'day'|'month') or (None, None)."""
    if not value:
        return None, None
    text = str(value).strip()
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), "day"
    long_form = re.search(
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text,
    )
    if long_form and long_form.group(2).lower() in MONTHS:
        return (
            date(int(long_form.group(3)), MONTHS[long_form.group(2).lower()], int(long_form.group(1))),
            "day",
        )
    month_year = re.search(r"([A-Za-z]+)\s+(\d{4})", text)
    if month_year and month_year.group(1).lower() in MONTHS:
        return date(int(month_year.group(2)), MONTHS[month_year.group(1).lower()], 1), "month"
    return None, None


def same_dob(left, right):
    left_date, left_prec = parse_date(left)
    right_date, right_prec = parse_date(right)
    if not left_date or not right_date:
        return False
    if left_prec == "day" and right_prec == "day":
        return left_date == right_date
    return left_date.year == right_date.year and left_date.month == right_date.month


def different_dob(left, right):
    left_date, _left_prec = parse_date(left)
    right_date, _right_prec = parse_date(right)
    if not left_date or not right_date:
        return False
    return not same_dob(left, right)


def parse_amounts(text):
    amounts = []
    for match in MONEY_RE.finditer(text or ""):
        amounts.append(float(match.group(1).replace(",", "") + "." + match.group(2)))
    return amounts


def near(left, right, tolerance=0.05):
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= tolerance


def any_amount(doc, amount, tolerance=0.05):
    return any(near(found, amount, tolerance) for found in doc["amounts"])


def _split_table_row(line):
    parts = [part.strip() for part in line.strip().strip("|").split("|")]
    return parts


def _parse_tables(lines):
    tables = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("|"):
            index += 1
            continue
        block = []
        while index < len(lines) and lines[index].startswith("|"):
            block.append(lines[index])
            index += 1
        if len(block) < 2:
            continue
        header = _split_table_row(block[0])
        rows = []
        for raw in block[2:]:
            cells = _split_table_row(raw)
            if len(cells) < len(header):
                cells = cells + [""] * (len(header) - len(cells))
            rows.append({header[col]: cells[col] for col in range(len(header))})
        tables.append(rows)
    return tables


def parse_document(meta, text):
    fields = {}
    lines = text.splitlines()
    for line in lines:
        match = FIELD_RE.match(line.strip())
        if match:
            fields[match.group(1).strip()] = match.group(2).strip()
    accounts = []
    for found in ACCOUNT_RE.findall(text):
        if found not in accounts:
            accounts.append(found)
    for value in fields.values():
        for found in ACCOUNT_RE.findall(value):
            if found not in accounts:
                accounts.append(found)
    return {
        "doc_id": meta["doc_id"],
        "type": meta["type"],
        "title": meta.get("title") or "",
        "dated": meta.get("dated") or "",
        "text": text,
        "fields": fields,
        "amounts": parse_amounts(text),
        "tables": _parse_tables(lines),
        "accounts": accounts,
        "label": "",
    }


def field(doc, *names):
    wanted = {norm(name) for name in names}
    for key, value in doc["fields"].items():
        if norm(key) in wanted and value:
            return value
    return ""


def table_rows(doc):
    rows = []
    for table in doc["tables"]:
        rows.extend(table)
    return rows


def load_docs(folder, metas):
    docs = []
    for meta in metas:
        path = os.path.join(folder, meta["file"])
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        docs.append(parse_document(meta, text))
    return docs


def baseline_gbp(customer, alerts):
    for alert in alerts:
        measured = alert.get("measured") or {}
        if measured.get("baseline_gbp"):
            return float(measured["baseline_gbp"])
    declared = customer.get("declared") or {}
    if declared.get("monthly_income_gbp"):
        return float(declared["monthly_income_gbp"])
    if declared.get("annual_turnover_gbp"):
        return float(declared["annual_turnover_gbp"]) / 12.0
    return None


def build_case(bundle, policy, case, narratives):
    customer = bundle["customers_by_id"][case["customer_id"]]
    alerts = []
    for alert_id in case["alert_ids"]:
        alerts.append(bundle["alerts_by_id"][alert_id])
    txns = bundle["txns_by_customer"].get(case["customer_id"], [])
    tx_by_id = {txn["transaction_id"]: txn for txn in txns}
    flagged = {}
    for alert in alerts:
        flagged[alert["alert_id"]] = [
            tx_by_id[txn_id]
            for txn_id in alert.get("transaction_ids") or []
            if txn_id in tx_by_id
        ]
    held = None
    if case.get("held_payment_id"):
        held = bundle["txns_by_id"].get(case["held_payment_id"])
    docs = load_docs(bundle["folder"], bundle["evidence_by_customer"].get(case["customer_id"], []))
    for doc in docs:
        doc["label"] = narratives.get(doc["doc_id"], "")
    list_entries = {}
    for alert in alerts:
        if alert["rule_id"] == "TM22":
            entry_id = (alert.get("measured") or {}).get("list_entry")
            if entry_id and entry_id in bundle["list_by_id"]:
                list_entries[entry_id] = bundle["list_by_id"][entry_id]
    return {
        "institution": bundle["institution"],
        "policy": policy,
        "case": case,
        "customer": customer,
        "alerts": alerts,
        "alert_ids": list(case["alert_ids"]),
        "rules": [alert["rule_id"] for alert in alerts],
        "txns": txns,
        "flagged": flagged,
        "held": held,
        "docs": docs,
        "reviews": bundle["reviews_by_customer"].get(case["customer_id"], []),
        "list_entries": list_entries,
        "baseline": baseline_gbp(customer, alerts),
        "known_ids": _known_ids(docs, txns, list_entries, bundle),
    }


def _known_ids(docs, txns, list_entries, bundle):
    known = {doc["doc_id"] for doc in docs}
    known.update(txn["transaction_id"] for txn in txns)
    known.update(list_entries)
    known.update(bundle["list_by_id"])
    return known


def docs_of(case, *types):
    wanted = set(types)
    return [doc for doc in case["docs"] if doc["type"] in wanted]


def alert_txns(case, rule_id):
    rows = []
    for alert in case["alerts"]:
        if alert["rule_id"] == rule_id:
            rows.extend(case["flagged"].get(alert["alert_id"], []))
    return rows


def material_txns(txns):
    kept = []
    for txn in txns:
        channel = txn.get("channel") or ""
        if channel in {"card", "direct_debit", "standing_order"}:
            continue
        reference = (txn.get("reference") or "").upper()
        if reference.startswith("SALARY") or reference in {"TOP UP", "MONTHLY CONTRIBUTION"}:
            continue
        kept.append(txn)
    return kept


def customer_name(case):
    return case["customer"].get("name") or ""


def is_customer_party(case, name):
    return party_overlap(name or "", customer_name(case))


def expected_countries(case):
    declared = case["customer"].get("declared") or {}
    return {norm(country) for country in declared.get("expected_countries") or []}


def business_activity(case):
    return case["customer"].get("business_activity") or ""


def employer(case):
    return case["customer"].get("employer") or ""


def txn_date(txn):
    parsed, _precision = parse_date((txn.get("booked_at") or "")[:10])
    return parsed


def unit_token(address):
    match = re.search(r"unit\s+([0-9]+[a-z]?)", norm(address))
    return match.group(1) if match else ""
