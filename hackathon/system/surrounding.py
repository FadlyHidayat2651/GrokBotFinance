"""LLM surrounding layer for residual / hard cases.

The deterministic engine decides first. This module only runs when that first
pass lands on a weak residue (mainly unexplained escalation). The model may:

- label free-text documents the phrase list missed
- name specific missing information the customer should provide

It must never emit CLOSE / RFI / ESCALATE. After optional labels are applied we
re-run decide(). If the case is still unexplained and the model named concrete
missing info, we may safely downgrade unexplained escalation to an RFI ask.
CLOSE still comes only from decide().
"""

from __future__ import annotations

import json
import os

import llm
from analyze import LABELS, MODEL_LABELS, classify_text
from config import CODES_FOR
from decide import decide

# First-pass outcomes that count as "engine did not fully settle the case".
TRIGGER_CODES = {
    "ESC_UNEXPLAINED_FUNDS",
    "RFI_SOURCE_OF_FUNDS",
    "RFI_PURPOSE_DOCUMENTS",
}

ALLOWED_DOC_LABELS = set(LABELS) | set(MODEL_LABELS) | {"unknown", "benign"}
FREE_TEXT_TYPES = {
    "support_chat",
    "staff_note",
    "email",
    "customer_email",
    "customer_letter",
    "venue_email",
}


def enrich_if_needed(case, decision, allow_model):
    """Return (case, decision) after an optional surrounding pass."""
    if not allow_model:
        return case, decision
    if os.environ.get("DISABLE_SURROUNDING") == "1":
        return case, decision
    if not _should_trigger(decision):
        return case, decision

    payload = llm.complete_json(_prompt(case, decision))
    if not isinstance(payload, dict):
        return case, decision

    applied = _apply_doc_labels(case, payload.get("documents") or [])
    missing_info = _clean_missing(payload.get("missing_info") or [])
    findings = _clean_findings(payload.get("findings") or [])

    case["surrounding"] = {
        "triggered": True,
        "labels_applied": applied,
        "missing_info": missing_info,
        "findings": findings,
        "suggested_reason_codes": _legal_codes(payload.get("suggested_reason_codes") or []),
    }

    # Re-run the engine with any new labels. CLOSE can only appear here via decide.
    second = decide(case)
    if second.get("reason_code") != "ESC_UNEXPLAINED_FUNDS":
        return case, second

    # Still unexplained: if the model named a concrete ask, prefer RFI over a
    # blank escalation. Never invent CLOSE.
    if missing_info:
        return case, _rfi_from_missing(case, second, missing_info)
    return case, second


def _should_trigger(decision):
    code = decision.get("reason_code") or ""
    if code in TRIGGER_CODES:
        return True
    return (decision.get("verdict") == "ESCALATE") and code == "ESC_UNEXPLAINED_FUNDS"


def _prompt(case, decision):
    docs = []
    for doc in case.get("docs") or []:
        if doc.get("type") not in FREE_TEXT_TYPES and not doc.get("label"):
            # Keep structured papers short: title + fields only.
            docs.append(
                {
                    "doc_id": doc.get("doc_id"),
                    "type": doc.get("type"),
                    "title": (doc.get("title") or "")[:120],
                    "label": doc.get("label") or "",
                    "text": "",
                }
            )
            continue
        docs.append(
            {
                "doc_id": doc.get("doc_id"),
                "type": doc.get("type"),
                "title": (doc.get("title") or "")[:120],
                "label": doc.get("label") or "",
                "text": (doc.get("text") or "")[:1800],
            }
        )

    alerts = []
    for alert in case.get("alerts") or []:
        txns = case.get("flagged", {}).get(alert["alert_id"], [])[:4]
        alerts.append(
            {
                "alert_id": alert.get("alert_id"),
                "rule_id": alert.get("rule_id"),
                "measured": alert.get("measured") or {},
                "sample_txns": [
                    {
                        "transaction_id": txn.get("transaction_id"),
                        "direction": txn.get("direction"),
                        "amount_gbp": txn.get("amount_gbp"),
                        "counterparty_name": txn.get("counterparty_name"),
                        "counterparty_country": txn.get("counterparty_country"),
                        "booked_at": (txn.get("booked_at") or "")[:10],
                        "reference": txn.get("reference"),
                    }
                    for txn in txns
                ],
            }
        )

    customer = case.get("customer") or {}
    instructions = {
        "task": (
            "The deterministic AML triage engine could not fully settle this case. "
            "Investigate only. Do not choose CLOSE, RFI or ESCALATE. "
            "Return JSON with document labels (free-text only), short findings, "
            "and concrete missing_info the customer could provide."
        ),
        "engine_first_pass": {
            "verdict": decision.get("verdict"),
            "reason_code": decision.get("reason_code"),
            "evidence": decision.get("evidence") or [],
        },
        "allowed_labels": sorted(ALLOWED_DOC_LABELS),
        "label_guide": {
            "mule": "Customer receives other people's money and passes it on.",
            "third_party_use": "Others invest or move money through the account.",
            "structuring": "Splitting or staying under a threshold to avoid checks.",
            "corridor_refusal": "Will not explain a higher-risk country payment.",
            "returned_cash": "Cash is their own earlier withdrawal paid back in.",
            "sale_story": "Money from selling a vehicle or other asset.",
            "gift_story": "Money is a gift from a relative.",
            "friend_loan": "A friend lent the money.",
            "family_vague": "Only vague family support, no document.",
            "medical": "Named medical treatment.",
            "benign": "Routine text matching none of the above.",
            "unknown": "Cannot label from the text.",
        },
        "customer": {
            "name": customer.get("name"),
            "customer_id": customer.get("customer_id"),
            "declared": customer.get("declared") or {},
            "business_activity": customer.get("business_activity"),
        },
        "alerts": alerts,
        "documents": docs[:40],
        "response_shape": {
            "documents": [{"doc_id": "DOC", "label": "benign"}],
            "findings": ["one short factual sentence"],
            "missing_info": ["exact document or answer to ask the customer for"],
            "suggested_reason_codes": ["RFI_SOURCE_OF_FUNDS"],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "You are an AML investigation assistant surrounding a rule engine. "
                "Reply with JSON only. Never output a case verdict."
            ),
        },
        {"role": "user", "content": json.dumps(instructions, ensure_ascii=False, sort_keys=True)},
    ]


def _apply_doc_labels(case, rows):
    applied = []
    if not isinstance(rows, list):
        return applied
    by_id = {doc["doc_id"]: doc for doc in case.get("docs") or []}
    for row in rows:
        if not isinstance(row, dict):
            continue
        doc_id = row.get("doc_id")
        label = str(row.get("label") or "").strip()
        if doc_id not in by_id or label not in ALLOWED_DOC_LABELS or label in {"", "unknown"}:
            continue
        doc = by_id[doc_id]
        if doc.get("type") not in FREE_TEXT_TYPES:
            continue
        # Do not overwrite a stronger phrase-list label.
        if doc.get("label") and classify_text(doc.get("text") or ""):
            continue
        if label == "benign" and doc.get("label"):
            continue
        if label not in MODEL_LABELS and label not in {"benign"}:
            # Only suspicion / story labels affect decide(); keep others out.
            if label not in LABELS:
                continue
        doc["label"] = label
        applied.append({"doc_id": doc_id, "label": label})
    return applied


def _clean_missing(rows):
    cleaned = []
    for item in rows:
        text = str(item or "").strip()
        if text and text not in cleaned:
            cleaned.append(text[:240])
        if len(cleaned) >= 3:
            break
    return cleaned


def _clean_findings(rows):
    cleaned = []
    for item in rows:
        text = str(item or "").strip()
        if text and text not in cleaned:
            cleaned.append(text[:240])
        if len(cleaned) >= 4:
            break
    return cleaned


def _legal_codes(rows):
    allowed = CODES_FOR["RFI"] | CODES_FOR["ESCALATE"]
    cleaned = []
    for item in rows:
        code = str(item or "").strip()
        if code in allowed and code not in cleaned:
            cleaned.append(code)
    return cleaned


def _rfi_from_missing(case, decision, missing_info):
    ask = missing_info[0]
    name = (case.get("customer") or {}).get("name") or "the customer"
    rules = {alert.get("rule_id") for alert in case.get("alerts") or []}
    if "TM22" in rules:
        code = "RFI_COUNTERPARTY_DETAILS"
    elif "TM01" in rules:
        code = "RFI_UPDATED_PROFILE"
    elif rules & {"TM04", "TM05", "TM07", "TM08"}:
        code = "RFI_PURPOSE_DOCUMENTS"
    else:
        code = "RFI_SOURCE_OF_FUNDS"
    evidence = list(decision.get("evidence") or [])
    sentences = [
        "The documents and payments on file do not fully explain every alert for %s." % name,
        "Ask %s for: %s" % (name, ask.rstrip(".")),
    ]
    if len(missing_info) > 1:
        sentences.append("Also request: %s." % missing_info[1].rstrip("."))
    else:
        sentences.append("There is no clear suspicion yet, so the case stays as a request for information.")
    return {
        "verdict": "RFI",
        "reason_code": code,
        "evidence": evidence,
        "sentences": sentences,
    }
