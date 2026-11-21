"""Policy decision engine. Escalation signals first, then RFI, then CLOSE only if every alert is explained."""

import re

from facts import (
    any_amount,
    business_activity,
    content_words,
    customer_name,
    different_dob,
    docs_of,
    expected_countries,
    field,
    gbp,
    is_customer_party,
    material_txns,
    near,
    norm,
    parse_date,
    party_overlap,
    same_dob,
    table_rows,
    txn_date,
    unit_token,
)

FAMILIES = (
    {"electronic", "electronics", "component", "components", "handset", "handsets", "phone", "phones"},
    {"catering", "kitchen", "food", "foods", "restaurant", "restaurants", "produce"},
    {"timber", "wood", "building", "materials"},
    {"clothing", "apparel", "garment", "garments"},
    {"freight", "shipping", "carrier", "shipment", "shipments"},
    {"furniture", "furnishing", "fittings"},
    {"wine", "wines"},
    {"machine", "machines", "parts"},
    {"medical", "hospital", "surgery"},
)

CLOSE_RANK = [
    "LIST_MATCH_DIFFERENT_PARTY",
    "INVOICE_MATCHES",
    "DOCUMENTED_EVENT",
    "REFUND_OF_EARLIER_PAYMENT",
    "COLLECTION_DOCUMENTED",
    "BUSINESS_RECORDS_RECONCILE",
    "THIRD_PARTY_DOCUMENTED",
    "GROUP_COMPANY",
    "OWN_ACCOUNT",
    "RETURNING_CUSTOMER",
    "WITHIN_PROFILE",
]

ESC_RANK = [
    "ESC_MULE_ACTIVITY",
    "ESC_LISTED_PARTY",
    "ESC_STRUCTURING",
    "ESC_ACCOUNT_MISUSE",
    "ESC_HIGH_RISK_CORRIDOR",
    "ESC_INVOICE_MISMATCH",
    "ESC_EARLY_REDEMPTION",
    "ESC_THIRD_PARTY_FUNDING",
    "ESC_PASS_THROUGH",
    "ESC_UNEXPLAINED_FUNDS",
]


def decide(case):
    suspicion = _suspicions(case)
    if suspicion:
        return suspicion
    explanations = _explanations(case)
    covered = set()
    used = []
    for item in explanations:
        if item["alerts"] - covered:
            used.append(item)
            covered |= item["alerts"]
    if set(case["alert_ids"]) <= covered and used:
        gated = _gates(case, used)
        if gated:
            return gated
        return _merge_close(used)
    return _residue(case, covered)


def _decision(verdict, code, evidence, sentences):
    return {
        "verdict": verdict,
        "reason_code": code,
        "evidence": [item for item in evidence if item],
        "sentences": [sentence for sentence in sentences if sentence],
    }


def _rules(case):
    return {alert["rule_id"] for alert in case["alerts"]}


def _ids(case, rules):
    return [alert["alert_id"] for alert in case["alerts"] if alert["rule_id"] in rules]


def _labels(case):
    return {doc["label"] for doc in case["docs"] if doc.get("label")}


def _person_match(left, right):
    if not left or not right:
        return False
    if norm(left) == norm(right):
        return True
    return party_overlap(left, right) and len(set(norm(left).split()) & set(norm(right).split())) >= 2


def _is_crypto(name):
    folded = norm(name)
    return "coinvale" in folded or "crypto" in folded


def _related(words_a, words_b):
    if words_a & words_b:
        return True
    for family in FAMILIES:
        if words_a & family and words_b & family:
            return True
    return False


def _flagged_flat(case):
    rows = []
    for alert in case["alerts"]:
        rows.extend(case["flagged"].get(alert["alert_id"], []))
    seen = set()
    unique = []
    for txn in rows:
        if txn["transaction_id"] not in seen:
            seen.add(txn["transaction_id"])
            unique.append(txn)
    return unique


def _material(case, txns):
    kept = []
    for txn in material_txns(txns):
        channel = txn.get("channel") or ""
        if channel in {"wallet_transfer", "cash_withdrawal"} and float(txn["amount_gbp"]) < 250:
            continue
        kept.append(txn)
    return kept


def _account_match(doc, txn):
    account = (txn.get("counterparty_account") or "").replace(" ", "")
    if not account:
        return False
    for candidate in doc["accounts"]:
        compact = candidate.replace(" ", "")
        if norm(candidate) == norm(txn.get("counterparty_account") or ""):
            return True
        digits = re.findall(r"\d{3,8}", candidate)
        if digits and account.endswith(digits[-1]) and "ending" in candidate.lower():
            return True
        if compact and (compact in account or account in compact) and len(compact) >= 8:
            return True
    return False


def _holder_is_customer(case, doc):
    holder = field(doc, "Account holder", "Account holders")
    if holder and (is_customer_party(case, holder) or _person_match(holder, customer_name(case))):
        return True
    text = doc["text"].lower()
    name = customer_name(case).lower()
    return bool(name and name in text and "held by" in text)


def _statement_hit(case, doc, txn):
    if not any_amount(doc, txn["amount_gbp"]):
        return False
    if not _holder_is_customer(case, doc):
        return False
    if _account_match(doc, txn):
        return True
    reference = (txn.get("reference") or "").upper()
    account_name = field(doc, "Account name")
    if account_name and party_overlap(account_name, txn.get("counterparty_name") or ""):
        return True
    if is_customer_party(case, txn.get("counterparty_name") or "") and (
        "OWN" in reference or "SAVING" in reference or "TRANSFER" in reference or "ISA" in reference
    ):
        return True
    if "held by" in doc["text"].lower() and "OWN" in reference:
        return True
    return False


_COMPANY = re.compile(
    r"\b(ltd|limited|plc|llp|inc|corp|co|company|gmbh|sarl|sa|bv|sal|llc|pty|holdings|manufacturing|industries|trading)\b"
    r"|sp\.?\s*z\.?\s*o\.?\s*o",
    re.IGNORECASE,
)


def _is_company(name):
    return bool(_COMPANY.search(name or ""))


def _ordinary_out(case, txn, newcos):
    """A supplier payment in a country the customer declared. It does not explain an alert on its own."""
    if txn.get("direction") != "out":
        return False
    name = txn.get("counterparty_name") or ""
    if _is_crypto(name):
        return False
    for company in newcos:
        if party_overlap(company, name):
            return False
    country = norm(txn.get("counterparty_country") or "")
    allowed = expected_countries(case) | {"united kingdom"}
    if country and country not in allowed:
        return False
    reference = (txn.get("reference") or "").upper()
    trade_ref = bool(re.search(r"\b(PO|INV|ORDER|INVOICE|BALANCE|CARRIER|FREIGHT)\b", reference))
    return _is_company(name) or trade_ref


def _rest_within_profile(case, alert, matched_ids):
    """True when the documented inflows account for the excess over the profile limit."""
    if alert["rule_id"] != "TM01":
        return False
    measured = alert.get("measured") or {}
    baseline = float(measured.get("baseline_gbp") or 0)
    if baseline <= 0:
        return False
    limit = float(measured.get("limit_multiple") or 2)
    received = float(measured.get("received_gbp") or 0)
    matched_sum = 0.0
    for txn in case["flagged"].get(alert["alert_id"], []):
        if txn["transaction_id"] in matched_ids and txn.get("direction") == "in":
            matched_sum += float(txn["amount_gbp"])
    if matched_sum <= 0:
        return False
    return (received - matched_sum) <= baseline * limit + 1.0


def _alert_covered(case, alert, matched_ids, newcos):
    material = _material(case, case["flagged"].get(alert["alert_id"], []))
    if not material:
        return False

    def ok(txn):
        if txn["transaction_id"] in matched_ids:
            return True
        if _ordinary_out(case, txn, newcos):
            return True
        if (
            alert["rule_id"] == "TM07"
            and txn.get("direction") == "in"
            and is_customer_party(case, txn.get("counterparty_name") or "")
        ):
            return True
        return False

    if all(ok(txn) for txn in material):
        return True
    if not _rest_within_profile(case, alert, matched_ids):
        return False
    for txn in material:
        if is_customer_party(case, txn.get("counterparty_name") or ""):
            continue
        if txn["transaction_id"] in matched_ids or _ordinary_out(case, txn, newcos):
            continue
        return False
    return True


def _who(txn):
    if not txn:
        return "the counterparty"
    name = txn.get("counterparty_name")
    if name:
        return name
    if "cash" in (txn.get("channel") or ""):
        return "cash"
    return "the counterparty"


def _customer_directors(case):
    names = set()
    for doc in docs_of(case, "company_register_extract"):
        company = field(doc, "Company")
        if company and not party_overlap(company, customer_name(case)):
            continue
        for row in table_rows(doc):
            for key in ("Officer", "Person with significant control"):
                if row.get(key):
                    names.add(norm(row[key]))
    return names


def _newco_names(case):
    directors = _customer_directors(case)
    names = []
    for doc in docs_of(case, "company_register_extract"):
        company = field(doc, "Company")
        if not company or party_overlap(company, customer_name(case)):
            continue
        if "dissolved" in doc["text"].lower():
            continue
        officers = []
        for row in table_rows(doc):
            for key in ("Officer", "Person with significant control"):
                if row.get(key):
                    officers.append(row[key])
        if not any(norm(officer) in directors for officer in officers):
            continue
        incorporated = field(doc, "Incorporated") or doc["text"]
        if "2026" in incorporated:
            names.append(company)
    return names


def _dissolved_names(case):
    names = []
    for doc in docs_of(case, "company_register_extract"):
        if "dissolved" not in doc["text"].lower():
            continue
        company = field(doc, "Company")
        if company:
            names.append(company)
    return names


def _goods_text(doc):
    parts = []
    for key in ("Goods", "Description", "Service"):
        value = field(doc, key)
        if value:
            parts.append(value)
    for row in table_rows(doc):
        for key, value in row.items():
            if norm(key) in {"goods", "description", "service", "work"} and value:
                parts.append(value)
    return " ".join(parts)


def _assess_list(case):
    """Return status, evidence ids, and sentences. status is same, different or unsettled."""
    alerts = [alert for alert in case["alerts"] if alert["rule_id"] == "TM22"]
    if not alerts:
        return "unsettled", [], []
    statuses = []
    evidence = []
    sentences = []
    for alert in alerts:
        measured = alert.get("measured") or {}
        entry_id = measured.get("list_entry")
        entry = case["list_entries"].get(entry_id) or {}
        txns = case["flagged"].get(alert["alert_id"], [])
        txn = txns[0] if txns else None
        pay_date = txn_date(txn) if txn else None
        status, extra_ids, extra_sentences = _one_list(case, alert, entry, txn, pay_date)
        statuses.append(status)
        evidence.extend(extra_ids)
        sentences.extend(extra_sentences)
    if "same" in statuses:
        return "same", evidence, sentences
    if statuses and all(status == "different" for status in statuses):
        return "different", evidence, sentences
    return "unsettled", evidence, sentences


def _one_list(case, alert, entry, txn, pay_date):
    entry_name = entry.get("name") or ""
    entry_id = entry.get("entry_id") or (alert.get("measured") or {}).get("list_entry") or ""
    evidence = [entry_id]
    if txn:
        evidence.append(txn["transaction_id"])
    controlled = None
    control_bits = []
    for doc in docs_of(case, "significant_control_register", "company_register_extract"):
        for row in table_rows(doc):
            person = row.get("Person") or row.get("Officer") or ""
            if not _person_match(person, entry_name):
                continue
            evidence.append(doc["doc_id"])
            if "Ceased" in row:
                ceased = (row.get("Ceased") or "").strip()
                ceased_date, _precision = parse_date(ceased)
                if not ceased:
                    controlled = True
                    control_bits.append(
                        "%s shows %s still holding %s, with no ceased date."
                        % (doc["doc_id"], person, row.get("Control") or "control")
                    )
                elif pay_date and ceased_date and ceased_date >= pay_date:
                    controlled = True
                    control_bits.append(
                        "%s shows %s ceased control on %s, which is on or after the payment on %s."
                        % (doc["doc_id"], person, ceased, txn.get("booked_at", "")[:10] if txn else "")
                    )
                elif pay_date and ceased_date and ceased_date < pay_date:
                    if controlled is not True:
                        controlled = False
                    control_bits.append(
                        "%s shows %s ceased control on %s, before the payment."
                        % (doc["doc_id"], person, ceased)
                    )
            resigned = (row.get("Resigned") or "").strip()
            if "Resigned" in row and not resigned:
                controlled = True
                control_bits.append("%s still lists %s as an officer." % (doc["doc_id"], person))
            elif resigned:
                control_bits.append("%s shows %s resigned on %s." % (doc["doc_id"], person, resigned))
    gift_status = None
    for doc in docs_of(case, "gift_letter"):
        if entry_name and entry_name.lower() not in doc["text"].lower():
            continue
        born = re.search(
            r"(?:born(?:\s+on)?|date of birth|dob)\s*:?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            doc["text"],
            flags=re.IGNORECASE,
        )
        if not born or not entry.get("date_of_birth"):
            continue
        evidence.append(doc["doc_id"])
        if same_dob(born.group(1), entry.get("date_of_birth")):
            gift_status = "same"
            control_bits.append(
                "Gift letter %s gives %s's date of birth as %s, the same date as list entry %s (%s)."
                % (doc["doc_id"], entry_name, born.group(1), entry_id, entry.get("date_of_birth"))
            )
        elif different_dob(born.group(1), entry.get("date_of_birth")):
            gift_status = "different"
            control_bits.append(
                "Gift letter %s gives %s's date of birth as %s, which is not list entry %s (%s)."
                % (doc["doc_id"], entry_name, born.group(1), entry_id, entry.get("date_of_birth"))
            )
    if gift_status == "same" or controlled is True:
        amount = gbp(txn["amount_gbp"]) if txn else "the payment"
        payee = txn.get("counterparty_name") if txn else (alert.get("measured") or {}).get("matched_party")
        when = (txn.get("booked_at") or "")[:10] if txn else ""
        sentences = [
            "The %s payment on %s to %s matched internal list entry %s (%s) on %s."
            % (
                amount,
                when,
                payee,
                entry_id,
                entry_name,
                (alert.get("measured") or {}).get("matched_on") or "a listed detail",
            ),
            " ".join(control_bits[:2]) or "The documents show the listed party is the counterparty or still controlled it.",
            "The listed party is the counterparty or still controlled it at the time of the payment, so the match is escalated.",
        ]
        return "same", evidence, sentences
    if gift_status == "different" or controlled is False:
        amount = gbp(txn["amount_gbp"]) if txn else "the payment"
        payee = txn.get("counterparty_name") if txn else ""
        when = (txn.get("booked_at") or "")[:10] if txn else ""
        sentences = [
            "The %s payment on %s to %s matched internal list entry %s on %s."
            % (amount, when, payee, entry_id, (alert.get("measured") or {}).get("matched_on") or "a listed detail"),
            " ".join(control_bits[:2]) or "The file shows the listed person was not in control at the payment date.",
            "List entry %s is %s, born %s. The evidence shows a different party, or that control had already ended."
            % (entry_id, entry_name, entry.get("date_of_birth") or "an unknown date"),
        ]
        return "different", evidence, sentences
    matched_on = ((alert.get("measured") or {}).get("matched_on") or "").lower()
    if "address" in matched_on and txn:
        payee = txn.get("counterparty_name") or ""
        list_address = entry.get("address") or ""
        company_address = ""
        officer_line = ""
        for doc in docs_of(case, "company_register_extract"):
            if payee and payee.lower() not in doc["text"].lower() and not party_overlap(field(doc, "Company"), payee):
                continue
            evidence.append(doc["doc_id"])
            company_address = field(doc, "Registered office") or company_address
            for row in table_rows(doc):
                if row.get("Officer"):
                    officer_line = "%s, born %s" % (row.get("Officer"), row.get("Born") or "unknown")
                    if entry.get("date_of_birth") and row.get("Born") and different_dob(row.get("Born"), entry["date_of_birth"]):
                        sentences = [
                            "The %s payment to %s matched list entry %s because the registered address mentions the same works."
                            % (gbp(txn["amount_gbp"]), payee, entry_id),
                            "Register %s gives the office as %s and the officer as %s."
                            % (doc["doc_id"], company_address or "the address on the extract", officer_line),
                            "List entry %s is %s, born %s, of %s. The officer's birth date does not match, so this is a different party."
                            % (entry_id, entry_name, entry.get("date_of_birth"), list_address or "the listed address"),
                        ]
                        return "different", evidence, sentences
        list_unit = unit_token(list_address)
        company_unit = unit_token(company_address)
        officer_name = officer_line.split(",")[0] if officer_line else ""
        if list_unit and company_unit and list_unit != company_unit and officer_name and not _person_match(officer_name, entry_name):
            receipt_ids = [doc["doc_id"] for doc in docs_of(case, "receipt", "invoice")]
            evidence.extend(receipt_ids)
            sentences = [
                "The %s payment on %s to %s matched list entry %s on a shared works address."
                % (gbp(txn["amount_gbp"]), (txn.get("booked_at") or "")[:10], payee, entry_id),
                "The company extract shows %s at Unit %s, officer %s, while list entry %s is %s of Unit %s (%s)."
                % (
                    payee,
                    company_unit.upper(),
                    officer_line or "another person",
                    entry_id,
                    entry_name,
                    list_unit.upper(),
                    list_address,
                ),
                "The units and the people differ, so the address match is a different party.",
            ]
            return "different", evidence, sentences
        if officer_name and _person_match(officer_name, entry_name):
            return "same", evidence, [
                "The payment to %s matched list entry %s on the address, and the officer is %s."
                % (payee, entry_id, officer_name),
                "That is the listed person %s, born %s." % (entry_name, entry.get("date_of_birth") or "the listed date"),
                "The listed party is connected to the counterparty, so the match is escalated.",
            ]
    amount = gbp(txn["amount_gbp"]) if txn else "the flagged amount"
    payee = txn.get("counterparty_name") if txn else (alert.get("measured") or {}).get("matched_party")
    when = (txn.get("booked_at") or "")[:10] if txn else ""
    sentences = [
        "The %s payment on %s to %s matched internal list entry %s on %s."
        % (amount, when, payee, entry_id, (alert.get("measured") or {}).get("matched_on") or "one detail"),
        "List entry %s is %s, born %s, %s."
        % (entry_id, entry_name, entry.get("date_of_birth") or "with no date of birth on the list", entry.get("address") or "with no address on the list"),
        "Nothing on file gives a date of birth or address that shows whether %s is that listed person." % (payee or "the counterparty"),
    ]
    return "unsettled", evidence, sentences


def _suspicions(case):
    labels = _labels(case)
    flagged = _flagged_flat(case)
    name = customer_name(case)

    frauds = docs_of(case, "sending_bank_fraud_report")
    if frauds or "mule" in labels:
        evidence = [doc["doc_id"] for doc in frauds]
        evidence += [doc["doc_id"] for doc in case["docs"] if doc.get("label") == "mule"]
        evidence += [txn["transaction_id"] for txn in flagged if _is_crypto(txn.get("counterparty_name") or "")][:4]
        incoming = [txn for txn in flagged if txn.get("direction") == "in" and not is_customer_party(case, txn.get("counterparty_name") or "")]
        bit = ""
        if frauds:
            bit = "Fraud report %s says a sending bank's customer was scammed into paying this account." % frauds[0]["doc_id"]
        elif "mule" in labels:
            bit = "In the chat, %s says other people's payments are passed on, including to a crypto account." % name
        sentences = [
            "%s's account took in %d payments from other people and moved funds straight back out."
            % (name, len(incoming)),
            bit or "The customer describes passing on other people's money.",
            "That is mule activity: the account is being used to receive and forward funds for someone else.",
        ]
        return _decision("ESCALATE", "ESC_MULE_ACTIVITY", evidence, sentences)

    if "third_party_use" in labels:
        chat = next(doc for doc in case["docs"] if doc.get("label") == "third_party_use")
        evidence = [chat["doc_id"]] + [txn["transaction_id"] for txn in flagged[:6]]
        rules = _rules(case)
        if "TM10" in rules:
            code = "ESC_EARLY_REDEMPTION"
            tail = "Fund units were bought and sold back quickly so that other people's money could be moved through the account."
        elif "TM06" in rules:
            code = "ESC_THIRD_PARTY_FUNDING"
            tail = "The money came from other people in a higher-risk corridor and was not the customer's own investment."
        else:
            code = "ESC_PASS_THROUGH"
            tail = "The funds were received from other people and sent on, including where the destination is a crypto exchange."
        payers = sorted({txn.get("counterparty_name") or "" for txn in flagged if txn.get("direction") == "in" and not is_customer_party(case, txn.get("counterparty_name") or "")})
        sentences = [
            "%s told the firm, in %s, that other people are using the account." % (name, chat["doc_id"]),
            "The incoming names include %s." % (", ".join(payers[:4]) or "several third parties"),
            tail,
        ]
        return _decision("ESCALATE", code, evidence, sentences)

    device = _device_problem(case)
    if device and (_rapid(case) or any(_is_crypto(txn.get("counterparty_name") or "") for txn in flagged)):
        evidence = [device["doc_id"]] + [txn["transaction_id"] for txn in flagged if _is_crypto(txn.get("counterparty_name") or "")][:3]
        sentences = [
            "Device export %s shows a device shared across customers or used from outside the United Kingdom." % device["doc_id"],
            "%s's account then received third-party funds and paid them out within the rapid-movement window." % name,
            "Someone other than the customer appears to be using the account.",
        ]
        return _decision("ESCALATE", "ESC_ACCOUNT_MISUSE", evidence, sentences)

    if "structuring" in labels:
        note = next(doc for doc in case["docs"] if doc.get("label") == "structuring")
        cash = [txn for txn in flagged if txn.get("channel") in {"cash_deposit", "cash_topup", "swift", "faster_payment"}]
        evidence = [note["doc_id"]] + [txn["transaction_id"] for txn in cash[:6]]
        amounts = ", ".join(gbp(txn["amount_gbp"]) for txn in cash[:4])
        sentences = [
            "Staff note %s records %s asking whether keeping transactions under the reporting or invoice threshold avoids checks."
            % (note["doc_id"], name),
            "The flagged amounts include %s." % (amounts or "a run of just-under-threshold payments"),
            "The payments were split to stay under the threshold, so the case is escalated as structuring.",
        ]
        return _decision("ESCALATE", "ESC_STRUCTURING", evidence, sentences)

    if "corridor_refusal" in labels:
        note = next(doc for doc in case["docs"] if doc.get("label") == "corridor_refusal")
        foreign = [txn for txn in flagged if norm(txn.get("counterparty_country") or "") in {norm(c) for c in case["policy"]["high_risk_countries"]}]
        evidence = [note["doc_id"]] + [txn["transaction_id"] for txn in foreign[:4]]
        country = foreign[0].get("counterparty_country") if foreign else "a higher-risk country"
        payee = foreign[0].get("counterparty_name") if foreign else "the beneficiary"
        total = sum(txn["amount_gbp"] for txn in foreign) if foreign else 0
        sentences = [
            "Staff note %s records that %s would not explain payments to %s." % (note["doc_id"], name, country),
            "The flagged payments total %s to %s." % (gbp(total), payee),
            "An unexplained higher-risk corridor payment is escalated.",
        ]
        return _decision("ESCALATE", "ESC_HIGH_RISK_CORRIDOR", evidence, sentences)

    story_labels = labels & {"gift_story", "sale_story", "friend_loan", "family_vague", "returned_cash"}
    if len(story_labels) >= 2 and "gift_story" in labels and ({"sale_story", "returned_cash"} & labels):
        docs = [doc["doc_id"] for doc in case["docs"] if doc.get("label") in story_labels]
        evidence = docs + [txn["transaction_id"] for txn in flagged[:4]]
        sentences = [
            "%s gave more than one explanation for the same cash, in %s." % (name, " and ".join(docs)),
            "One account calls it a gift and another calls it a sale or a returned withdrawal.",
            "The stories contradict each other and the funds moved on, so the source is not a credible explanation.",
        ]
        return _decision("ESCALATE", "ESC_UNEXPLAINED_FUNDS", evidence, sentences)

    dissolved = _dissolved_names(case)
    if dissolved:
        inbound = [
            txn
            for txn in flagged
            if txn.get("direction") == "in" and any(party_overlap(company, txn.get("counterparty_name") or "") for company in dissolved)
        ]
        if inbound and ("friend_loan" in labels or any(txn.get("direction") == "out" for txn in flagged)):
            evidence = [doc["doc_id"] for doc in docs_of(case, "company_register_extract") if "dissolved" in doc["text"].lower()]
            evidence += [txn["transaction_id"] for txn in inbound[:3]]
            outs = [txn for txn in flagged if txn.get("direction") == "out" and not is_customer_party(case, txn.get("counterparty_name") or "")]
            evidence += [txn["transaction_id"] for txn in outs[:2]]
            sentences = [
                "Register %s shows %s was already dissolved when it paid %s into %s's account."
                % (evidence[0], dissolved[0], gbp(inbound[0]["amount_gbp"]), name),
                "The customer described the money as a loan from a friend, which the register contradicts."
                if "friend_loan" in labels
                else "The funds were paid on to another party.",
                "Money from a dissolved company that was moved on is escalated as pass-through.",
            ]
            return _decision("ESCALATE", "ESC_PASS_THROUGH", evidence, sentences)

    status, evidence, sentences = _assess_list(case)
    if status == "same":
        return _decision("ESCALATE", "ESC_LISTED_PARTY", evidence, sentences)

    newcos = _newco_names(case)
    if newcos and "TM02" in _rules(case):
        paid = [
            txn
            for txn in flagged
            if txn.get("direction") == "out" and any(party_overlap(company, txn.get("counterparty_name") or "") for company in newcos)
        ]
        if paid and not docs_of(case, "group_structure"):
            evidence = [doc["doc_id"] for doc in docs_of(case, "company_register_extract", "consulting_contract")]
            evidence += [txn["transaction_id"] for txn in paid[:4]]
            sentences = [
                "%s received third-party 'consulting' receipts and paid %s on to %s."
                % (name, gbp(sum(txn["amount_gbp"] for txn in paid)), " and ".join(newcos[:2])),
                "The registers show those companies were formed in 2026 and are controlled by the same director as the customer.",
                "The consulting contract on file does not match these payers or these amounts. The pattern is pass-through to linked companies.",
            ]
            return _decision("ESCALATE", "ESC_PASS_THROUGH", evidence, sentences)

    mismatch = _invoice_mismatch(case)
    if mismatch:
        return mismatch
    return None


def _device_problem(case):
    for doc in docs_of(case, "device_export"):
        for row in table_rows(doc):
            customers = row.get("Customers who logged in from it") or ""
            countries = row.get("Login countries") or ""
            ids = [part.strip() for part in customers.split(",") if part.strip()]
            foreign = [
                part.strip()
                for part in countries.split(",")
                if part.strip() and norm(part) not in {"united kingdom", "uk"}
            ]
            if len(ids) >= 2 or foreign:
                return doc
    return None


def _rapid(case):
    return "TM02" in _rules(case)


def _invoice_mismatch(case):
    activity = business_activity(case)
    activity_words = content_words(activity)
    if not activity_words:
        return None
    contracts = docs_of(case, "supply_contract", "consulting_contract", "contract_schedule")
    for doc in docs_of(case, "invoice", "supplier_invoice", "supplier_invoices"):
        goods = _goods_text(doc)
        goods_words = content_words(goods)
        if not goods_words or _related(goods_words, activity_words):
            continue
        supplier = field(doc, "Supplier", "Pay to", "Payee") or doc["title"]
        if any(party_overlap(supplier, contract["text"]) or party_overlap(supplier, contract["title"]) for contract in contracts):
            continue
        if sum(doc["amounts"] or [0]) < 1000 and not doc["amounts"]:
            continue
        evidence = [doc["doc_id"]]
        evidence += [txn["transaction_id"] for txn in _flagged_flat(case)[:4]]
        sentences = [
            "Invoice %s describes %s." % (doc["doc_id"], goods[:180]),
            "%s's stated business is %s." % (customer_name(case), activity),
            "The invoice does not support that business, and there is no contract tying the supplier to the customer's trade. The case is escalated for the mismatch.",
        ]
        return _decision("ESCALATE", "ESC_INVOICE_MISMATCH", evidence, sentences)
    return None


def _explanations(case):
    found = []
    for builder in (
        _explain_list_different,
        _explain_records,
        _explain_booking,
        _explain_sale,
        _explain_returned_cash,
        _explain_events,
        _explain_loan_invoice,
        _explain_schedule,
        _explain_freight,
        _explain_payment_file,
        _explain_purchase_order,
        _explain_invoices,
        _explain_refunds,
        _explain_gift,
        _explain_group,
        _explain_factoring,
        _explain_cancellation,
        _explain_tenancy,
        _explain_employment,
        _explain_own_account,
        _explain_declared_freight,
    ):
        item = builder(case)
        if item and item["alerts"]:
            found.append(item)
    return found


def _exp(code, alerts, evidence, sentences, strength="strong", own_account=False):
    return {
        "code": code,
        "alerts": set(alerts),
        "evidence": evidence,
        "sentences": sentences,
        "strength": strength,
        "own_account": own_account,
    }


def _explain_list_different(case):
    status, evidence, sentences = _assess_list(case)
    if status != "different":
        return None
    return _exp("LIST_MATCH_DIFFERENT_PARTY", _ids(case, {"TM22"}), evidence, sentences)


def _row_amounts(doc):
    amounts = []
    for row in table_rows(doc):
        for value in row.values():
            amounts.extend([amount for amount in __amounts(value)])
    return amounts


def __amounts(value):
    return [float(match.group(1).replace(",", "") + "." + match.group(2)) for match in re.finditer(r"£\s*([0-9,]*)\.(\d{2})", value or "")]


def _cash_txns(case):
    return [
        txn
        for txn in _flagged_flat(case)
        if txn.get("channel") in {"cash_deposit", "cash_topup", "cash_withdrawal"}
    ]


def _explain_records(case):
    docs = docs_of(case, "cash_collection_reports", "cash_reconciliation", "takings_book", "ticket_report")
    if not docs:
        return None
    cash = _cash_txns(case)
    if not cash:
        return None
    matched = []
    used_docs = []
    for doc in docs:
        amounts = doc["amounts"] or _row_amounts(doc)
        hits = [txn for txn in cash if any(near(amount, txn["amount_gbp"]) for amount in amounts)]
        if hits:
            matched.extend(hits)
            used_docs.append(doc["doc_id"])
    matched_ids = {txn["transaction_id"] for txn in matched}
    if len(matched_ids) < max(1, int(0.8 * len(cash))):
        return None
    total = sum(txn["amount_gbp"] for txn in cash)
    sentences = [
        "Business records %s list cash amounts that match the flagged deposits." % (", ".join(used_docs)),
        "Those deposits total %s across %d payments, including %s on %s."
        % (gbp(total), len(cash), gbp(cash[0]["amount_gbp"]), (cash[0].get("booked_at") or "")[:10]),
        "The takings or collection records reconcile to the activity, so the below-threshold pattern is the way these sites bank their own cash.",
    ]
    evidence = used_docs + [txn["transaction_id"] for txn in cash[:8]]
    alerts = []
    for alert in case["alerts"]:
        if alert["rule_id"] in {"TM03", "TM05"}:
            alerts.append(alert["alert_id"])
        elif alert["rule_id"] == "TM01":
            material = _material(case, case["flagged"][alert["alert_id"]])
            if material and all(txn["transaction_id"] in matched_ids for txn in material):
                alerts.append(alert["alert_id"])
    return _exp("BUSINESS_RECORDS_RECONCILE", alerts, evidence, sentences)


def _explain_booking(case):
    docs = docs_of(case, "booking_confirmation")
    if not docs:
        return None
    doc = docs[0]
    travellers = re.findall(r"^\s*\d+\.\s+(.+)$", doc["text"], flags=re.MULTILINE)
    price = field(doc, "Price")
    price_amounts = __amounts(price) or doc["amounts"]
    per_person = price_amounts[0] if price_amounts else None
    total = price_amounts[-1] if len(price_amounts) > 1 else None
    payee_line = field(doc, "Payment")
    incoming = [
        txn
        for txn in _flagged_flat(case)
        if txn.get("direction") == "in" and not is_customer_party(case, txn.get("counterparty_name") or "")
    ]
    named = [
        txn
        for txn in incoming
        if any(_person_match(txn.get("counterparty_name") or "", traveller) for traveller in travellers)
        and (per_person is None or near(txn["amount_gbp"], per_person, 0.05))
    ]
    outgoing = [
        txn
        for txn in _flagged_flat(case)
        if txn.get("direction") == "out"
        and (
            party_overlap(txn.get("counterparty_name") or "", doc["text"])
            or (total is not None and near(txn["amount_gbp"], total, 0.05))
        )
    ]
    if len(named) < max(3, int(0.6 * max(len(travellers), 1))) or not outgoing:
        return None
    evidence = [doc["doc_id"], outgoing[0]["transaction_id"]] + [txn["transaction_id"] for txn in named[:8]]
    sentences = [
        "Booking %s is a group trip for %s, %s per person, paid to the travel company named in the booking."
        % (doc["doc_id"], customer_name(case), gbp(per_person or named[0]["amount_gbp"])),
        "%d of the travellers paid that amount into the account, including %s."
        % (len(named), named[0].get("counterparty_name")),
        "The %s payment to %s matches the booking total. The receipts are a collection for named people."
        % (gbp(outgoing[0]["amount_gbp"]), outgoing[0].get("counterparty_name")),
    ]
    return _exp("COLLECTION_DOCUMENTED", _ids(case, {"TM02", "TM04", "TM01"}), evidence, sentences)


def _explain_sale(case):
    docs = docs_of(case, "vehicle_sale_receipt", "sale_receipt")
    if not docs:
        return None
    cash = [txn for txn in _cash_txns(case) if txn.get("direction") == "in" or txn.get("channel") != "cash_withdrawal"]
    for doc in docs:
        seller = field(doc, "Seller") or customer_name(case)
        if not is_customer_party(case, seller) and customer_name(case).lower() not in doc["text"].lower():
            continue
        hits = [txn for txn in cash if any_amount(doc, txn["amount_gbp"])]
        if not hits:
            continue
        support = [extra["doc_id"] for extra in docs_of(case, "insurance_schedule", "advert")]
        evidence = [doc["doc_id"]] + support + [txn["transaction_id"] for txn in hits]
        sentences = [
            "%s records that %s received %s." % (doc["doc_id"], customer_name(case), gbp(hits[0]["amount_gbp"])),
            "The cash paid in on %s is the same amount." % ((hits[0].get("booked_at") or "")[:10]),
            "Insurance or the advert on file refers to the same asset."
            if support
            else "The receipt is a documented sale that explains the cash.",
        ]
        alerts = _ids(case, {"TM05"})
        for alert in case["alerts"]:
            if alert["rule_id"] == "TM01":
                material = _material(case, case["flagged"][alert["alert_id"]])
                if all(txn["transaction_id"] in {hit["transaction_id"] for hit in hits} for txn in material):
                    alerts.append(alert["alert_id"])
        return _exp("DOCUMENTED_EVENT", alerts, evidence, sentences)
    return None


def _explain_returned_cash(case):
    chats = [doc for doc in case["docs"] if doc.get("label") == "returned_cash"]
    if not chats:
        return None
    if _labels(case) & {"gift_story", "sale_story", "friend_loan"}:
        return None
    amounts = chats[0]["amounts"]
    deposits = [txn for txn in _cash_txns(case) if txn.get("channel") in {"cash_deposit", "cash_topup"}]
    if not deposits:
        return None
    deposit = deposits[0]
    target = deposit["amount_gbp"]
    if amounts and not any(near(amount, target, 0.05) for amount in amounts):
        target = amounts[0]
    withdrawal = None
    for txn in case["txns"]:
        if txn.get("channel") == "cash_withdrawal" and near(txn["amount_gbp"], target, 0.05):
            if not withdrawal or txn.get("booked_at", "") < withdrawal.get("booked_at", ""):
                withdrawal = txn
    if not withdrawal:
        return None
    evidence = [chats[0]["doc_id"], deposit["transaction_id"], withdrawal["transaction_id"]]
    sentences = [
        "In %s, %s says the %s cash paid in on %s is the same cash withdrawn earlier."
        % (chats[0]["doc_id"], customer_name(case), gbp(deposit["amount_gbp"]), (deposit.get("booked_at") or "")[:10]),
        "Cash withdrawal %s on %s was %s."
        % (withdrawal["transaction_id"], (withdrawal.get("booked_at") or "")[:10], gbp(withdrawal["amount_gbp"])),
        "The deposit puts that withdrawal back, after the purchase it was taken for did not go ahead.",
    ]
    alerts = _ids(case, {"TM05"})
    for alert in case["alerts"]:
        if alert["rule_id"] == "TM01":
            material = _material(case, case["flagged"][alert["alert_id"]])
            if all(txn["transaction_id"] == deposit["transaction_id"] for txn in material):
                alerts.append(alert["alert_id"])
    return _exp("DOCUMENTED_EVENT", alerts, evidence, sentences)


def _party_amount_hit(doc, txn):
    if not any_amount(doc, txn["amount_gbp"]):
        return False
    if _account_match(doc, txn):
        return True
    name = txn.get("counterparty_name") or ""
    if name and (party_overlap(name, doc["text"]) or party_overlap(name, doc["title"])):
        return True
    reference = txn.get("reference") or ""
    if reference and len(reference) > 4 and reference.lower() in doc["text"].lower():
        return True
    return False


def _explain_events(case):
    matched = []
    evidence = []
    sentences = []
    kind = "DOCUMENTED_EVENT"
    for doc in docs_of(case, "solicitor_letter", "insurer_letter", "settlement_letter", "completion_statement"):
        hits = [txn for txn in _flagged_flat(case) if txn.get("direction") == "in" and _party_amount_hit(doc, txn)]
        if not hits and doc["type"] == "completion_statement":
            paid = field(doc, "Paid to client's investment account")
            paid_amounts = __amounts(paid)
            client = field(doc, "Client")
            hits = [
                txn
                for txn in _flagged_flat(case)
                if txn.get("direction") == "in"
                and paid_amounts
                and near(txn["amount_gbp"], paid_amounts[0])
                and (not client or is_customer_party(case, client) or client.lower() in doc["text"].lower())
                and party_overlap(txn.get("counterparty_name") or "", doc["title"] + " " + doc["text"][:200])
            ]
        if not hits:
            continue
        matched.extend(hits)
        evidence.append(doc["doc_id"])
        evidence.extend(txn["transaction_id"] for txn in hits)
        sentences.append(
            "%s documents %s from %s to %s on %s."
            % (
                doc["doc_id"],
                gbp(hits[0]["amount_gbp"]),
                hits[0].get("counterparty_name"),
                customer_name(case),
                (hits[0].get("booked_at") or "")[:10],
            )
        )
    redundancy = docs_of(case, "redundancy_letter")
    if redundancy and "TM10" in _rules(case):
        doc = redundancy[0]
        employer = case["customer"].get("employer") or ""
        if not employer or employer.lower() in doc["text"].lower() or "redundant" in doc["text"].lower():
            evidence.append(doc["doc_id"])
            for email in docs_of(case, "customer_email"):
                evidence.append(email["doc_id"])
            alerts = _ids(case, {"TM10"})
            sentences.append(
                "Redundancy letter %s confirms %s's role at %s ended, which is why the fund units were sold."
                % (doc["doc_id"], customer_name(case), employer or "the employer named in the letter")
            )
            sentences.append("The early redemption is the documented consequence of that redundancy, not a pass-through of someone else's money.")
            return _exp(kind, alerts, evidence, sentences or [
                "A redundancy letter explains the early redemption.",
                "The customer asked to sell the holding because of that redundancy.",
            ])
    if not matched:
        return None
    matched_ids = {txn["transaction_id"] for txn in matched}
    alerts = []
    for alert in case["alerts"]:
        if alert["rule_id"] not in {"TM01", "TM07", "TM08", "TM06"}:
            continue
        material = _material(case, case["flagged"][alert["alert_id"]])
        if material and all(txn["transaction_id"] in matched_ids for txn in material):
            alerts.append(alert["alert_id"])
    if not sentences:
        return None
    sentences.append("The amount, the payer and the date line up with the payment, so the inflow is a documented one-off event.")
    return _exp(kind, alerts, evidence, sentences[:4])


def _explain_loan_invoice(case):
    loans = docs_of(case, "loan_agreement")
    invoices = docs_of(case, "dealer_invoice", "invoice")
    if not loans or not invoices:
        return None
    loan_hits = []
    invoice_hits = []
    evidence = []
    for doc in loans:
        hits = [txn for txn in _flagged_flat(case) if txn.get("direction") == "in" and _party_amount_hit(doc, txn)]
        if hits:
            loan_hits.extend(hits)
            evidence.append(doc["doc_id"])
    for doc in invoices:
        hits = [txn for txn in _flagged_flat(case) if txn.get("direction") == "out" and _party_amount_hit(doc, txn)]
        if hits:
            invoice_hits.extend(hits)
            evidence.append(doc["doc_id"])
    if not loan_hits or not invoice_hits:
        return None
    evidence += [txn["transaction_id"] for txn in loan_hits + invoice_hits]
    sentences = [
        "Loan agreement %s matches the %s received from %s."
        % (loans[0]["doc_id"], gbp(loan_hits[0]["amount_gbp"]), loan_hits[0].get("counterparty_name")),
        "Invoice %s matches the %s paid to %s."
        % (invoices[0]["doc_id"], gbp(invoice_hits[0]["amount_gbp"]), invoice_hits[0].get("counterparty_name")),
        "The rapid movement is the loan going on to the seller named in the invoice.",
    ]
    return _exp("INVOICE_MATCHES", _ids(case, {"TM01", "TM02"}), evidence, sentences)


def _explain_schedule(case):
    docs = docs_of(case, "contract_schedule")
    if not docs:
        return None
    doc = docs[0]
    supplier = field(doc, "Supplier")
    rows = table_rows(doc)
    outs = [txn for txn in _flagged_flat(case) if txn.get("direction") == "out"]
    matched = []
    for row in rows:
        amounts = []
        for value in row.values():
            amounts.extend(__amounts(value))
        for txn in outs:
            if amounts and any(near(amount, txn["amount_gbp"]) for amount in amounts):
                if not supplier or party_overlap(supplier, txn.get("counterparty_name") or "") or party_overlap(supplier, doc["text"]):
                    matched.append(txn)
    matched_ids = {txn["transaction_id"] for txn in matched}
    alert_txns = [txn for txn in _flagged_flat(case) if txn.get("direction") == "out"]
    if len(matched_ids) < max(2, int(0.75 * max(len(alert_txns), 1))):
        return None
    sentences = [
        "Contract schedule %s sets stage payments to %s." % (doc["doc_id"], supplier or "the supplier"),
        "The flagged payments match those stage amounts, including %s." % gbp(matched[0]["amount_gbp"]),
        "The amounts were set by the contract, not split to stay under a threshold.",
    ]
    return _exp("INVOICE_MATCHES", _ids(case, {"TM03"}), [doc["doc_id"]] + list(matched_ids), sentences)


def _explain_freight(case):
    carriers = docs_of(case, "carrier_invoice")
    freights = docs_of(case, "freight_invoices")
    if not carriers or not freights:
        return None
    evidence = [carriers[0]["doc_id"], freights[0]["doc_id"]]
    in_hits = []
    for row in table_rows(freights[0]):
        exporter = row.get("Exporter") or ""
        amounts = []
        for value in row.values():
            amounts.extend(__amounts(value))
        for txn in _flagged_flat(case):
            if txn.get("direction") == "in" and amounts and any(near(amount, txn["amount_gbp"]) for amount in amounts):
                if not exporter or party_overlap(exporter, txn.get("counterparty_name") or ""):
                    in_hits.append(txn)
    out_hits = [txn for txn in _flagged_flat(case) if txn.get("direction") == "out" and _party_amount_hit(carriers[0], txn)]
    if not in_hits or not out_hits:
        return None
    evidence += [txn["transaction_id"] for txn in in_hits + out_hits]
    sentences = [
        "Freight invoices %s match the amounts received from the exporters, including %s from %s."
        % (freights[0]["doc_id"], gbp(in_hits[0]["amount_gbp"]), in_hits[0].get("counterparty_name")),
        "Carrier invoice %s matches the %s paid to %s."
        % (carriers[0]["doc_id"], gbp(out_hits[0]["amount_gbp"]), out_hits[0].get("counterparty_name")),
        "%s's business is freight forwarding, and these documents are the charges collected and the carrier payment."
        % customer_name(case),
    ]
    return _exp("INVOICE_MATCHES", _ids(case, {"TM02", "TM08", "TM01"}), evidence, sentences)


def _explain_payment_file(case):
    docs = docs_of(case, "payment_file")
    if not docs:
        return None
    doc = docs[0]
    matched = []
    for row in table_rows(doc):
        payee = row.get("Payee") or row.get("Contractor") or ""
        amounts = []
        for value in row.values():
            amounts.extend(__amounts(value))
        for txn in _flagged_flat(case):
            if txn.get("direction") != "out" or not amounts:
                continue
            if any(near(amount, txn["amount_gbp"]) for amount in amounts) and (
                not payee or _person_match(payee, txn.get("counterparty_name") or "") or party_overlap(payee, txn.get("counterparty_name") or "")
            ):
                matched.append(txn)
    if len({txn["transaction_id"] for txn in matched}) < 5:
        return None
    newcos = _newco_names(case)
    outs = [txn for txn in _flagged_flat(case) if txn.get("direction") == "out"]
    unmatched_outs = [
        txn
        for txn in outs
        if txn["transaction_id"] not in {hit["transaction_id"] for hit in matched}
        and not _ordinary_out(case, txn, newcos)
        and float(txn["amount_gbp"]) >= 250
    ]
    alerts = _ids(case, {"TM04"})
    evidence = [doc["doc_id"]] + [txn["transaction_id"] for txn in matched[:10]]
    pool = [
        txn
        for txn in _flagged_flat(case)
        if txn.get("direction") == "in"
        and "COMMISSION" in (txn.get("reference") or "").upper()
    ]
    file_total = sum(txn["amount_gbp"] for txn in matched)
    sentences = [
        "Payment file %s lists the beneficiaries and amounts, including %s to %s."
        % (doc["doc_id"], gbp(matched[0]["amount_gbp"]), matched[0].get("counterparty_name")),
        "%d flagged payments match that file." % len({txn["transaction_id"] for txn in matched}),
    ]
    if pool and near(pool[0]["amount_gbp"], file_total, max(1.0, 0.25 * file_total)):
        alerts += _ids(case, {"TM08", "TM02"})
        evidence.append(pool[0]["transaction_id"])
        sentences.append(
            "The %s commission-pool receipt from %s funds those listed payments."
            % (gbp(pool[0]["amount_gbp"]), pool[0].get("counterparty_name"))
        )
    elif not unmatched_outs:
        alerts += _ids(case, {"TM02"})
        sentences.append("The other outgoing payments in the alert are supplier payments to countries the customer declared.")
    else:
        sentences.append("The file is the instruction for the individual payments that caused the many-beneficiary alert.")
    return _exp("INVOICE_MATCHES", alerts, evidence, sentences[:4])


def _explain_invoices(case):
    docs = docs_of(
        case,
        "invoice",
        "supplier_invoice",
        "supplier_invoices",
        "hospital_invoice",
        "dealer_invoice",
        "purchase_order",
    )
    if not docs:
        return None
    matched = []
    evidence = []
    notes = []
    for doc in docs:
        hits = [txn for txn in _flagged_flat(case) if _party_amount_hit(doc, txn)]
        if not hits and doc["type"] in {"invoice", "supplier_invoices"}:
            hits = _instalment_hits(doc, _flagged_flat(case))
        if not hits and doc["type"] == "purchase_order":
            continue
        if hits:
            matched.extend(hits)
            evidence.append(doc["doc_id"])
            notes.append(
                "%s matches %s with %s."
                % (doc["doc_id"], gbp(hits[0]["amount_gbp"]), hits[0].get("counterparty_name"))
            )
    if not matched:
        return None
    matched_ids = {txn["transaction_id"] for txn in matched}
    newcos = _newco_names(case)
    alerts = [
        alert["alert_id"]
        for alert in case["alerts"]
        if alert["rule_id"] in {"TM01", "TM02", "TM03", "TM06", "TM07", "TM08"}
        and _alert_covered(case, alert, matched_ids, newcos)
    ]
    if not alerts:
        return None
    sentences = notes[:2] + ["The invoice or order matches the amount, the payee and the date of the flagged payment."]
    return _exp("INVOICE_MATCHES", alerts, evidence + list(matched_ids), sentences)


def _explain_purchase_order(case):
    orders = docs_of(case, "purchase_order")
    if not orders:
        return None
    activity = content_words(business_activity(case))
    invoices = docs_of(case, "supplier_invoice", "supplier_invoices")
    funding = []
    for alert in case["alerts"]:
        if alert["rule_id"] != "TM01":
            continue
        for txn in _material(case, case["flagged"][alert["alert_id"]]):
            if txn.get("direction") == "in" and is_customer_party(case, txn.get("counterparty_name") or ""):
                funding.append(txn)
    if not funding:
        return None
    total = sum(float(txn["amount_gbp"]) for txn in funding)
    for order in orders:
        if not order["amounts"]:
            continue
        order_amt = max(order["amounts"])
        if abs(order_amt - total) > max(1.0, 0.12 * max(order_amt, total)):
            continue
        goods = order["text"]
        evidence = [order["doc_id"]]
        for invoice in invoices:
            goods += " " + _goods_text(invoice)
            evidence.append(invoice["doc_id"])
        if activity and not _related(content_words(goods), activity):
            continue
        evidence += [txn["transaction_id"] for txn in funding[:6]]
        goods_bit = _goods_text(invoices[0]) if invoices else "the goods named in the order"
        sentences = [
            "Purchase order %s is for %s. %s paid in %s from its own account in the review window."
            % (order["doc_id"], gbp(order_amt), customer_name(case), gbp(total)),
            "The supplier invoice on file is for %s, which matches the customer's trade."
            % (goods_bit[:160] or "the same order"),
            "The spike is the customer funding a documented order.",
        ]
        return _exp("INVOICE_MATCHES", _ids(case, {"TM01"}), evidence, sentences)
    return None


def _instalment_hits(doc, txns):
    if "instal" not in doc["text"].lower() and doc["type"] != "supplier_invoices":
        return []
    if not doc["amounts"]:
        return []
    target = max(doc["amounts"])
    groups = {}
    for txn in txns:
        if txn.get("direction") != "out":
            continue
        if not party_overlap(txn.get("counterparty_name") or "", doc["text"]):
            continue
        groups.setdefault(txn.get("counterparty_name"), []).append(txn)
    for rows in groups.values():
        if near(sum(txn["amount_gbp"] for txn in rows), target, 1.0) and len(rows) >= 2:
            return rows
    return []


def _explain_refunds(case):
    docs = docs_of(case, "venue_email", "credit_note")
    if not docs:
        return None
    matched = []
    evidence = []
    notes = []
    for doc in docs:
        hits = [
            txn
            for txn in _flagged_flat(case)
            if txn.get("direction") == "in" and _party_amount_hit(doc, txn)
        ]
        if not hits:
            continue
        matched.extend(hits)
        evidence.append(doc["doc_id"])
        notes.append(
            "%s refunds %s from %s, and the incoming payment on %s is that amount, reference %s."
            % (
                doc["doc_id"],
                gbp(hits[0]["amount_gbp"]),
                hits[0].get("counterparty_name"),
                (hits[0].get("booked_at") or "")[:10],
                hits[0].get("reference"),
            )
        )
    if not matched:
        return None
    matched_ids = {txn["transaction_id"] for txn in matched}
    newcos = _newco_names(case)
    alerts = [
        alert["alert_id"]
        for alert in case["alerts"]
        if alert["rule_id"] in {"TM01", "TM06", "TM08", "TM02"}
        and _alert_covered(case, alert, matched_ids, newcos)
    ]
    if not alerts:
        return None
    notes.append("The higher-risk or third-party receipt is a refund of an earlier payment, not a new unexplained flow.")
    return _exp("REFUND_OF_EARLIER_PAYMENT", alerts, evidence + list(matched_ids), notes[:3])


def _explain_gift(case):
    docs = docs_of(case, "gift_letter")
    if not docs:
        return None
    status, _list_evidence, _list_sentences = _assess_list(case)
    if status == "same":
        return None
    for doc in docs:
        hits = [
            txn
            for txn in _flagged_flat(case)
            if txn.get("direction") == "in" and any_amount(doc, txn["amount_gbp"]) and party_overlap(txn.get("counterparty_name") or "", doc["text"])
        ]
        if not hits:
            continue
        if customer_name(case).lower() not in doc["text"].lower():
            continue
        evidence = [doc["doc_id"], hits[0]["transaction_id"]]
        sentences = [
            "Gift letter %s says %s gave %s %s."
            % (doc["doc_id"], hits[0].get("counterparty_name"), customer_name(case), gbp(hits[0]["amount_gbp"])),
            "The payment on %s from %s is that amount, reference %s."
            % ((hits[0].get("booked_at") or "")[:10], hits[0].get("counterparty_name"), hits[0].get("reference")),
            "The letter documents the relationship and says the gift does not have to be repaid.",
        ]
        alerts = []
        matched_ids = {hits[0]["transaction_id"]}
        for alert in case["alerts"]:
            if alert["rule_id"] not in {"TM01", "TM08"}:
                continue
            material = _material(case, case["flagged"][alert["alert_id"]])
            if material and all(txn["transaction_id"] in matched_ids for txn in material):
                alerts.append(alert["alert_id"])
        if status == "different":
            alerts += _ids(case, {"TM22"})
        return _exp("THIRD_PARTY_DOCUMENTED", alerts, evidence, sentences)
    return None


def _group_pairs(case):
    pairs = []
    for doc in docs_of(case, "group_structure"):
        companies = []
        for row in table_rows(doc):
            company = row.get("Company") or ""
            owner = row.get("Owner") or row.get("Owned by") or ""
            if company:
                companies.append((company, owner, doc["doc_id"]))
        pairs.append(companies)
    return pairs


def _explain_group(case):
    groups = _group_pairs(case)
    if not groups:
        return None
    customer = customer_name(case)
    evidence = []
    inbound = []
    for companies in groups:
        doc_id = companies[0][2] if companies else ""
        names = [company for company, _owner, _doc in companies]
        owners = [owner for _company, owner, _doc in companies]
        related = set(names + owners)
        if not any(party_overlap(customer, item) for item in related):
            continue
        for txn in _flagged_flat(case):
            if txn.get("direction") != "in" or is_customer_party(case, txn.get("counterparty_name") or ""):
                continue
            if any(party_overlap(txn.get("counterparty_name") or "", item) for item in related):
                inbound.append(txn)
                evidence.append(doc_id)
    if not inbound:
        return None
    evidence = list(dict.fromkeys(evidence)) + [txn["transaction_id"] for txn in inbound]
    newcos = _newco_names(case)
    matched_ids = {txn["transaction_id"] for txn in inbound}
    alerts = []
    for alert in case["alerts"]:
        if alert["rule_id"] not in {"TM08", "TM02", "TM01"}:
            continue
        material = _material(case, case["flagged"][alert["alert_id"]])
        if material and all(txn["transaction_id"] in matched_ids or _ordinary_out(case, txn, newcos) for txn in material):
            alerts.append(alert["alert_id"])
    invoices = [
        doc["doc_id"]
        for doc in docs_of(case, "supplier_invoice", "invoice")
        if any(_party_amount_hit(doc, txn) for txn in _flagged_flat(case))
    ]
    evidence.extend(invoices)
    sentences = [
        "Group structure %s shows %s and %s under the same ownership."
        % (evidence[0], customer, inbound[0].get("counterparty_name")),
        "The %s received on %s is marked as intercompany funding."
        % (gbp(inbound[0]["amount_gbp"]), (inbound[0].get("booked_at") or "")[:10]),
        "Invoice %s matches the supplier payment that funding was for." % invoices[0]
        if invoices
        else "The other outgoing payments go to countries the customer declared for its trade.",
    ]
    return _exp("GROUP_COMPANY", alerts, evidence, sentences)


def _explain_factoring(case):
    agreements = docs_of(case, "factoring_agreement")
    remittances = docs_of(case, "remittance_advice")
    if not agreements or not remittances:
        return None
    hits = []
    evidence = []
    for doc in remittances:
        for txn in _flagged_flat(case):
            if txn.get("direction") == "in" and any_amount(doc, txn["amount_gbp"]) and party_overlap(txn.get("counterparty_name") or "", doc["text"]):
                hits.append(txn)
                evidence.append(doc["doc_id"])
    if not hits:
        return None
    evidence.append(agreements[0]["doc_id"])
    evidence += [txn["transaction_id"] for txn in hits]
    newcos = _newco_names(case)
    matched_ids = {txn["transaction_id"] for txn in hits}
    alerts = [
        alert["alert_id"]
        for alert in case["alerts"]
        if alert["rule_id"] in {"TM08", "TM01", "TM02"}
        and _alert_covered(case, alert, matched_ids, newcos)
    ]
    advance = field(remittances[0], "Advance at 85%") or gbp(hits[0]["amount_gbp"])
    sentences = [
        "Factoring agreement %s is with %s, which advances invoices into %s's account."
        % (agreements[0]["doc_id"], hits[0].get("counterparty_name"), customer_name(case)),
        "Remittance %s shows an advance of %s, and the payment on %s is that amount."
        % (remittances[0]["doc_id"], advance if advance.startswith("£") else gbp(hits[0]["amount_gbp"]), (hits[0].get("booked_at") or "")[:10]),
        "The third-party funding is the documented invoice-finance advance.",
    ]
    return _exp("THIRD_PARTY_DOCUMENTED", alerts, evidence, sentences)


def _explain_cancellation(case):
    docs = docs_of(case, "cancellation_confirmation")
    if not docs:
        return None
    doc = docs[0]
    if not doc["amounts"]:
        return None
    amount = doc["amounts"][0]
    legs = [
        txn
        for txn in _flagged_flat(case)
        if is_customer_party(case, txn.get("counterparty_name") or "") and near(txn["amount_gbp"], amount)
    ]
    if len(legs) < 2 and not (legs and _account_match(doc, legs[0])):
        # Still accept one matched leg plus the document saying the account is held in the customer's name.
        if not legs:
            return None
    evidence = [doc["doc_id"]] + [txn["transaction_id"] for txn in legs]
    sentences = [
        "Cancellation %s returns %s to %s's own account %s."
        % (doc["doc_id"], gbp(amount), customer_name(case), ", ".join(doc["accounts"][:1]) or "named in the confirmation"),
        "The subscription and the return are both %s with %s."
        % (gbp(legs[0]["amount_gbp"]), legs[0].get("counterparty_name")),
        "The rapid movement is the cooling-off refund of the customer's own subscription.",
    ]
    return _exp("REFUND_OF_EARLIER_PAYMENT", _ids(case, {"TM01", "TM02"}), evidence, sentences)


def _explain_tenancy(case):
    returns = docs_of(case, "deposit_return_letter")
    holdings = docs_of(case, "holding_deposit_receipt")
    if not returns or not holdings:
        return None
    in_hits = [txn for txn in _flagged_flat(case) if txn.get("direction") == "in" and _party_amount_hit(returns[0], txn)]
    out_hits = [txn for txn in _flagged_flat(case) if txn.get("direction") == "out" and _party_amount_hit(holdings[0], txn)]
    if not in_hits or not out_hits:
        return None
    evidence = [returns[0]["doc_id"], holdings[0]["doc_id"], in_hits[0]["transaction_id"], out_hits[0]["transaction_id"]]
    sentences = [
        "Letter %s returns a tenancy deposit of %s from %s."
        % (returns[0]["doc_id"], gbp(in_hits[0]["amount_gbp"]), in_hits[0].get("counterparty_name")),
        "Receipt %s is the %s holding deposit paid to %s the next day."
        % (holdings[0]["doc_id"], gbp(out_hits[0]["amount_gbp"]), out_hits[0].get("counterparty_name")),
        "The rapid movement is one tenancy ending and the next deposit being paid.",
    ]
    return _exp("DOCUMENTED_EVENT", _ids(case, {"TM02", "TM01"}), evidence, sentences)


def _explain_employment(case):
    contracts = docs_of(case, "employment_contract")
    statements = docs_of(case, "bank_statement")
    if not contracts or not statements:
        return None
    contract = contracts[0]
    if customer_name(case).lower() not in contract["text"].lower():
        return None
    hits = []
    used_statement = None
    for doc in statements:
        for txn in _flagged_flat(case):
            if _statement_hit(case, doc, txn):
                hits.append(txn)
                used_statement = doc
    if not hits:
        return None
    evidence = [contract["doc_id"], used_statement["doc_id"]] + [txn["transaction_id"] for txn in hits]
    sentences = [
        "Employment contract %s places %s in a role paid into a local account."
        % (contract["doc_id"], customer_name(case)),
        "Statement %s shows %s holds the account that sent %s on %s."
        % (used_statement["doc_id"], customer_name(case), gbp(hits[0]["amount_gbp"]), (hits[0].get("booked_at") or "")[:10]),
        "The corridor payment is a transfer from the customer's own account, documented by the statement.",
    ]
    matched_ids = {txn["transaction_id"] for txn in hits}
    alerts = []
    for alert in case["alerts"]:
        if alert["rule_id"] not in {"TM06", "TM01", "TM07"}:
            continue
        material = _material(case, case["flagged"][alert["alert_id"]])
        if material and all(txn["transaction_id"] in matched_ids for txn in material):
            alerts.append(alert["alert_id"])
    return _exp("OWN_ACCOUNT", alerts, evidence, sentences, own_account=True)


def _explain_own_account(case):
    statements = docs_of(case, "bank_statement")
    if not statements:
        return None
    hits = []
    used = []
    for doc in statements:
        for txn in _flagged_flat(case) + case["txns"]:
            if _statement_hit(case, doc, txn):
                if txn["transaction_id"] not in {hit["transaction_id"] for hit in hits}:
                    hits.append(txn)
                    used.append(doc["doc_id"])
    if not hits:
        return None
    hit_ids = {txn["transaction_id"] for txn in hits}
    # Own-account funding: inflows are the customer, and a statement proves the destination account.
    own_out = [
        txn
        for txn in hits
        if txn.get("direction") == "out" and (
            is_customer_party(case, txn.get("counterparty_name") or "") or "OWN" in (txn.get("reference") or "").upper()
        )
    ]
    alerts = []
    for alert in case["alerts"]:
        if alert["rule_id"] not in {"TM01", "TM02", "TM06", "TM07", "TM08"}:
            continue
        material = _material(case, case["flagged"][alert["alert_id"]])
        if not material:
            continue
        def covered(txn):
            if txn["transaction_id"] in hit_ids:
                return True
            if alert["rule_id"] == "TM01" and is_customer_party(case, txn.get("counterparty_name") or "") and own_out:
                return True
            return False
        if all(covered(txn) for txn in material):
            alerts.append(alert["alert_id"])
    if not alerts:
        return None
    reverification = docs_of(case, "reverification_record")
    code = "OWN_ACCOUNT"
    if reverification and "TM07" in _rules(case) and "TM01" not in _rules(case) and "TM02" not in _rules(case):
        code = "RETURNING_CUSTOMER"
    sample = hits[0]
    sentences = [
        "Statement %s shows %s holds the account used for the %s transfer on %s."
        % (used[0], customer_name(case), gbp(sample["amount_gbp"]), (sample.get("booked_at") or "")[:10]),
        "The counterparty and the reference show the movement is between accounts the customer holds.",
    ]
    evidence = list(dict.fromkeys(used)) + [txn["transaction_id"] for txn in hits[:6]]
    if reverification:
        evidence.append(reverification[0]["doc_id"])
        sentences.append(
            "Identity re-verification %s confirmed %s on the existing device before the account was used again."
            % (reverification[0]["doc_id"], customer_name(case))
        )
    else:
        sentences.append("No third-party payer is involved in the flagged movement.")
    return _exp(code, alerts, evidence, sentences[:4], own_account=True)


def _explain_declared_freight(case):
    activity = business_activity(case).lower()
    if "freight" not in activity and "shipping" not in activity:
        return None
    if docs_of(case, "carrier_invoice", "freight_invoices"):
        return None
    inbound = [txn for txn in _flagged_flat(case) if txn.get("direction") == "in" and "FREIGHT" in (txn.get("reference") or "").upper()]
    outbound = [txn for txn in _flagged_flat(case) if txn.get("direction") == "out" and "CARRIER" in (txn.get("reference") or "").upper()]
    if not inbound or not outbound:
        return None
    evidence = [txn["transaction_id"] for txn in inbound[:3] + outbound[:2]]
    sentences = [
        "%s declares freight forwarding: it collects shipping charges and pays carriers." % customer_name(case),
        "The receipts reference freight, including %s from %s, and %s is paid to %s."
        % (gbp(inbound[0]["amount_gbp"]), inbound[0].get("counterparty_name"), gbp(outbound[0]["amount_gbp"]), outbound[0].get("counterparty_name")),
        "There is no carrier invoice on file for the outgoing payment.",
    ]
    return _exp(
        "WITHIN_PROFILE",
        _ids(case, {"TM02", "TM08", "TM01"}),
        evidence,
        sentences,
        strength="weak",
    )


def _gates(case, used):
    profile_alerts = [alert for alert in case["alerts"] if alert["rule_id"] == "TM01"]
    if profile_alerts:
        multiple = float((profile_alerts[0].get("measured") or {}).get("multiple") or 0)
        covering = [item for item in used if profile_alerts[0]["alert_id"] in item["alerts"]]
        limit = case["policy"]["own_account_profile_multiple"]
        if covering and all(item.get("own_account") for item in covering) and multiple >= limit:
            sample = covering[0]
            return _decision(
                "RFI",
                "RFI_UPDATED_PROFILE",
                sample["evidence"],
                [
                    sample["sentences"][0],
                    "Those own-account transfers take 30-day receipts to %s times the declared baseline. The policy threshold for an updated profile is %s times."
                    % (round(multiple, 2), int(limit)),
                    "Ask %s for an updated expected income or payment volume that covers this level of own-account funding."
                    % customer_name(case),
                ],
            )
    limit = case["policy"].get("invoice_release_gbp")
    held = case["held"]
    if limit and held and float(held["amount_gbp"]) >= limit and not is_customer_party(case, held.get("counterparty_name") or ""):
        if not _held_documented(case, held, used):
            return _decision(
                "RFI",
                "RFI_PURPOSE_DOCUMENTS",
                [held["transaction_id"]],
                [
                    "The held payment is %s to %s on %s, reference %s."
                    % (gbp(held["amount_gbp"]), held.get("counterparty_name"), (held.get("booked_at") or "")[:10], held.get("reference")),
                    "Payments of %s or more to anyone other than the client need an invoice or contract before release."
                    % gbp(limit),
                    "Ask %s for the invoice or contract for this payment, showing the payee, the amount and what it is for."
                    % customer_name(case),
                ],
            )
    return None


def _held_documented(case, held, used):
    for item in used:
        if item.get("strength") == "weak":
            continue
        if held["transaction_id"] in item["evidence"]:
            return True
    for doc in docs_of(
        case,
        "invoice",
        "supplier_invoice",
        "supplier_invoices",
        "dealer_invoice",
        "hospital_invoice",
        "contract_schedule",
        "supply_contract",
        "carrier_invoice",
        "payment_file",
        "consulting_contract",
    ):
        if _party_amount_hit(doc, held):
            return True
    return False


def _merge_close(used):
    ranked = sorted(used, key=lambda item: CLOSE_RANK.index(item["code"]) if item["code"] in CLOSE_RANK else 99)
    strong = [item for item in ranked if item.get("strength") != "weak"]
    chosen = (strong or ranked)[0]
    sentences = []
    evidence = []
    for item in [chosen] + [other for other in ranked if other is not chosen]:
        for sentence in item["sentences"]:
            if sentence not in sentences:
                sentences.append(sentence)
        evidence.extend(item["evidence"])
    return _decision("CLOSE", chosen["code"], evidence, sentences[:4])


def _residue(case, covered):
    order = {"TM22": 0, "TM05": 1, "TM04": 2, "TM10": 3, "TM03": 4, "TM06": 5, "TM02": 6, "TM08": 7, "TM07": 8, "TM01": 9}
    missing = [alert for alert in case["alerts"] if alert["alert_id"] not in covered]
    missing.sort(key=lambda alert: order.get(alert["rule_id"], 9))
    options = []
    for alert in missing:
        options.append(_residue_one(case, alert))
    escalations = [item for item in options if item["verdict"] == "ESCALATE"]
    if escalations:
        escalations.sort(key=lambda item: ESC_RANK.index(item["reason_code"]) if item["reason_code"] in ESC_RANK else 99)
        return escalations[0]
    if options:
        return options[0]
    return _decision(
        "ESCALATE",
        "ESC_UNEXPLAINED_FUNDS",
        [txn["transaction_id"] for txn in _flagged_flat(case)[:4]],
        [
            "The alerts on %s are not explained by the documents on file." % customer_name(case),
            "The funds moved without a matching invoice, event or own-account record.",
            "The case is escalated because the activity remains unexplained.",
        ],
    )


def _residue_one(case, alert):
    rule = alert["rule_id"]
    txns = case["flagged"].get(alert["alert_id"], [])
    material = _material(case, txns) or txns
    evidence = [txn["transaction_id"] for txn in material[:6]]
    name = customer_name(case)
    sample = material[0] if material else (txns[0] if txns else None)
    amount = gbp(sample["amount_gbp"]) if sample else "the flagged amount"
    counterparty = sample.get("counterparty_name") if sample else "the counterparty"
    when = (sample.get("booked_at") or "")[:10] if sample else ""
    reference = sample.get("reference") if sample else ""

    if rule == "TM22":
        _status, list_evidence, list_sentences = _assess_list(case)
        return _decision(
            "RFI",
            "RFI_COUNTERPARTY_DETAILS",
            list_evidence or evidence,
            list_sentences
            or [
                "Ask %s who %s is, including their date of birth and address, and what the %s payment on %s was for."
                % (name, counterparty, amount, when),
                "The name matches an internal list entry and nothing on file settles whether it is the same party.",
                "There is no other suspicion on the case, so the question is only to identify the counterparty.",
            ],
        )
    if rule == "TM03":
        return _decision(
            "ESCALATE",
            "ESC_STRUCTURING",
            evidence,
            [
                "Alert %s shows repeated payments just under the threshold, totalling %s."
                % (alert["alert_id"], gbp((alert.get("measured") or {}).get("total_gbp") or 0)),
                "No contract schedule, takings book or collection record sets those amounts.",
                "The pattern is structuring and is escalated.",
            ],
        )
    if rule == "TM06":
        country = (alert.get("measured") or {}).get("countries") or (sample.get("counterparty_country") if sample else "a higher-risk country")
        return _decision(
            "ESCALATE",
            "ESC_HIGH_RISK_CORRIDOR",
            evidence,
            [
                "%s moved %s to or from %s, which was not declared and is higher risk."
                % (name, amount, country),
                "The payee is %s, reference %s, on %s." % (counterparty, reference, when),
                "No invoice, employment contract or own-account statement explains the corridor, so it is escalated.",
            ],
        )
    if rule == "TM10":
        third = [
            txn
            for txn in txns
            if txn.get("direction") == "out" and not is_customer_party(case, txn.get("counterparty_name") or "") and txn.get("channel") != "fund_subscription"
        ]
        measured = alert.get("measured") or {}
        if third or "third_party_use" in _labels(case):
            return _decision(
                "ESCALATE",
                "ESC_EARLY_REDEMPTION",
                evidence,
                [
                    "%s bought %s of %s and sold it %s days later."
                    % (name, gbp(measured.get("bought_gbp") or 0), measured.get("fund") or "the fund", measured.get("held_days")),
                    "The proceeds were paid to %s." % (third[0].get("counterparty_name") if third else "someone other than the customer"),
                    "No redundancy or other personal event explains the early sale. It was used to move money.",
                ],
            )
        return _decision(
            "RFI",
            "RFI_PURPOSE_DOCUMENTS",
            evidence,
            [
                "%s sold %s of %s after %s days, receiving %s."
                % (name, gbp(measured.get("bought_gbp") or 0), measured.get("fund") or "the fund", measured.get("held_days"), gbp(measured.get("redeemed_gbp") or 0)),
                "Nothing on file explains why the holding was sold inside 90 days.",
                "Ask %s for the reason for the sale and a document such as a redundancy letter if a personal event caused it."
                % name,
            ],
        )
    if rule == "TM02" and any(_is_crypto(txn.get("counterparty_name") or "") for txn in txns):
        return _decision(
            "ESCALATE",
            "ESC_PASS_THROUGH",
            evidence,
            [
                "%s received third-party funds and paid them to %s within the rapid-movement window."
                % (name, next(txn.get("counterparty_name") for txn in txns if _is_crypto(txn.get("counterparty_name") or ""))),
                "There is no invoice or contract for that pass-through.",
                "Money arriving and leaving for a crypto exchange, with no credible purpose, is escalated.",
            ],
        )
    if rule in {"TM01", "TM08"} and sample and _looks_documented_gap(sample):
        return _ask_source(case, sample, evidence)
    if rule == "TM08":
        return _decision(
            "RFI",
            "RFI_COUNTERPARTY_DETAILS",
            evidence,
            [
                "Ask %s who %s is and why they paid or received %s on %s (reference %s)."
                % (name, counterparty, amount, when, reference),
                "The name is not the customer's, and no gift letter, invoice-finance remittance or group chart is on file.",
                "One answer identifying the counterparty and the purpose would settle it. Nothing else on the case is suspicious.",
            ],
        )
    if rule == "TM05":
        sale = next((doc for doc in case["docs"] if doc.get("label") == "sale_story"), None)
        if sale:
            return _decision(
                "RFI",
                "RFI_SOURCE_OF_FUNDS",
                [sale["doc_id"]] + evidence,
                [
                    "In %s, %s says the %s cash paid in on %s is from a sale."
                    % (sale["doc_id"], name, amount, when),
                    "There is no sale receipt or advert on file for that amount.",
                    "Ask %s for the sale receipt or the buyer's details for this cash." % name,
                ],
            )
        return _decision(
            "RFI",
            "RFI_SOURCE_OF_FUNDS",
            evidence,
            [
                "Ask %s where the %s cash on %s at %s came from, and for a receipt or sale document."
                % (name, amount, when, sample.get("location") if sample else "the branch"),
                "The cash is above the institution's cash limit and no sale receipt or takings record matches it.",
                "A single document for that amount would settle the source.",
            ],
        )
    if rule == "TM07":
        return _decision(
            "RFI",
            "RFI_SOURCE_OF_FUNDS",
            evidence,
            [
                "The account was dormant and then %s moved, including %s %s on %s."
                % (gbp((alert.get("measured") or {}).get("moved_gbp") or 0), sample.get("direction") if sample else "", amount, when),
                "Ask %s for a statement showing they hold the account the funds came from, and for a fresh identity check if the account is being used again."
                % name,
                "The counterparty name is not enough on its own to treat the account as theirs.",
            ],
        )
    if rule in {"TM02", "TM04"} and _looks_like_trip(case):
        return _decision(
            "RFI",
            "RFI_PURPOSE_DOCUMENTS",
            evidence,
            [
                "Several people paid the same amount into %s's account and the money went on to a travel company."
                % name,
                "Ask %s for the booking confirmation, the traveller list and the amount due." % name,
                "There is no suspicion beyond the missing booking. The document would show whether this is a group collection.",
            ],
        )
    if rule == "TM04":
        return _decision(
            "RFI",
            "RFI_PURPOSE_DOCUMENTS",
            evidence,
            [
                "The account paid %s different beneficiaries in the review window."
                % ((alert.get("measured") or {}).get("distinct_counterparties") or "many"),
                "Ask %s for the payment file or contracts that name those beneficiaries and the amounts." % name,
                "No payment file is on the case. One file matching the payees would settle the alert.",
            ],
        )
    if rule in {"TM01", "TM02"} and sample and _looks_like_missing_invoice(sample):
        return _decision(
            "RFI",
            "RFI_PURPOSE_DOCUMENTS",
            evidence,
            [
                "The payment pattern points to a documented purchase or loan, but the papers are not on file.",
                "On %s, %s %s %s, reference %s."
                % (when, counterparty, sample.get("direction") if sample else "", amount, reference),
                "Ask %s for the invoice, loan agreement or sale contract that matches this amount and payee." % name,
            ],
        )
    if rule == "TM02" and _third_party_passthrough(case, txns):
        return _decision(
            "ESCALATE",
            "ESC_PASS_THROUGH",
            evidence,
            [
                "%s received funds from other people and paid most of them out within the rapid-movement window." % name,
                "The outgoing names include %s."
                % (", ".join(sorted({txn.get("counterparty_name") or "" for txn in txns if txn.get("direction") == "out"})[:3])),
                "No invoice, booking or own-account statement gives the payments a purpose.",
            ],
        )
    if rule == "TM01" and sample and is_customer_party(case, sample.get("counterparty_name") or ""):
        multiple = float((alert.get("measured") or {}).get("multiple") or 0)
        if multiple >= case["policy"]["own_account_profile_multiple"]:
            code = "RFI_UPDATED_PROFILE"
            ask = "Ask %s for an updated expected income or payment volume." % name
        else:
            code = "RFI_SOURCE_OF_FUNDS"
            ask = "Ask %s for a bank statement showing they hold the funding account, and confirm the expected volume." % name
        return _decision(
            "RFI",
            code,
            evidence,
            [
                "%s's own-name receipts in the window were %s, %s times the declared baseline."
                % (name, gbp((alert.get("measured") or {}).get("received_gbp") or 0), round(multiple, 2)),
                "No statement on file shows the other account is theirs.",
                ask,
            ],
        )
    if rule in {"TM01", "TM08", "TM02", "TM07"}:
        return _ask_source(case, sample, evidence)
    return _decision(
        "ESCALATE",
        "ESC_UNEXPLAINED_FUNDS",
        evidence,
        [
            "Alert %s (%s) is not covered by any document on file." % (alert["alert_id"], rule),
            "The sample payment is %s %s %s on %s." % (amount, sample.get("direction") if sample else "", counterparty, when),
            "The activity is unexplained and is escalated.",
        ],
    )


def _looks_documented_gap(txn):
    reference = (txn.get("reference") or "").upper()
    name = (txn.get("counterparty_name") or "").lower()
    if any(token in reference for token in ("ESTATE", "SALE PROCEEDS", "SETTLEMENT", "GIFT", "CLAIM", "ADVANCE")):
        return True
    if "conveyanc" in name or "solicitor" in name or "invoice finance" in name:
        return True
    if re.search(r"\b[a-z]\s*&\s*[a-z]\b", name):
        return True
    return False


def _looks_like_missing_invoice(txn):
    reference = (txn.get("reference") or "").upper()
    name = (txn.get("counterparty_name") or "").lower()
    if "LOAN" in reference or "INV" in reference:
        return True
    if "motor" in name or "finance" in name:
        return True
    return False


def _looks_like_trip(case):
    incoming = [
        txn
        for txn in _flagged_flat(case)
        if txn.get("direction") == "in" and not is_customer_party(case, txn.get("counterparty_name") or "")
    ]
    if len(incoming) < 4:
        return False
    amounts = [round(float(txn["amount_gbp"]), 2) for txn in incoming]
    common = max(set(amounts), key=amounts.count)
    if amounts.count(common) < 4:
        return False
    blob = " ".join((txn.get("reference") or "") + " " + (txn.get("counterparty_name") or "") for txn in _flagged_flat(case)).lower()
    return "trip" in blob or "travel" in blob


def _third_party_passthrough(case, txns):
    incoming = [
        txn
        for txn in txns
        if txn.get("direction") == "in" and not is_customer_party(case, txn.get("counterparty_name") or "")
    ]
    outgoing = [
        txn
        for txn in txns
        if txn.get("direction") == "out" and not is_customer_party(case, txn.get("counterparty_name") or "")
    ]
    return len(incoming) >= 3 and len(outgoing) >= 1


def _ask_source(case, sample, evidence):
    name = customer_name(case)
    if not sample:
        return _decision(
            "RFI",
            "RFI_SOURCE_OF_FUNDS",
            evidence,
            [
                "Ask %s for documents showing where the flagged funds came from." % name,
                "The papers that would normally evidence this pattern are not on file.",
                "One document matching the amount and the payer would settle the case.",
            ],
        )
    return _decision(
        "RFI",
        "RFI_SOURCE_OF_FUNDS",
        evidence,
        [
            "Ask %s for the document behind the %s %s with %s on %s, reference %s."
            % (
                name,
                gbp(sample["amount_gbp"]),
                sample.get("direction") or "payment",
                _who(sample),
                (sample.get("booked_at") or "")[:10],
                sample.get("reference") or "none",
            ),
            "That would be the completion statement, gift letter, settlement letter, claim letter or bank statement, whichever applies to this payment.",
            "Nothing else on the case suggests the money is being passed on for someone else.",
        ],
    )
