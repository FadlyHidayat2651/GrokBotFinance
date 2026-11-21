"""Phrase labels, then one specialist-agent call on every case.

The agent investigates. It does not return a verdict. A missing key or a failed
call leaves the phrase labels in place.
"""

import json
import threading

import llm
from facts import customer_name

LABELS = (
    "structuring",
    "corridor_refusal",
    "mule",
    "third_party_use",
    "returned_cash",
    "sale_story",
    "gift_story",
    "friend_loan",
    "family_vague",
    "medical",
    "benign",
)

# First matching label wins. Patterns are applied to the whole document.
PATTERNS = (
    (
        "structuring",
        (
            "under £",
            "below £",
            "below the £",
            "under the £",
            "aggregated",
            "aggregate",
            "exempt",
            "splitting",
            "breaking down",
            "broken down",
            "avoids reporting",
            "avoid reporting",
            "identity verification",
        ),
    ),
    (
        "corridor_refusal",
        (
            "unwilling",
            "unable or unwilling",
            "unable to provide",
            "would not explain",
            "would not provide",
            "no justification",
            "could not explain",
            "couldn't explain",
            "declined to",
            "refused to explain",
            "refused to provide",
            "remained evasive",
            "evasive",
        ),
    ),
    (
        "mule",
        (
            "payments assistant",
            "crypto account",
            "social media",
            "keep 8%",
            "keep 8 %",
            "i keep 8",
        ),
    ),
    (
        "third_party_use",
        (
            "investing through",
            "invest through",
            "invest for them",
            "invest for a few",
            "changed their minds",
            "cannot open an account",
            "can't open an account",
        ),
    ),
    (
        "returned_cash",
        (
            "fell through",
            "deposited that cash",
            "withdrew in",
            "same cash",
        ),
    ),
    ("sale_story", ("selling my car", "sold my car", "sale of my car", "from selling")),
    ("gift_story", ("gift from", "my uncle", "a gift")),
    ("friend_loan", (
        "loan from a friend",
        "loan from my friend",
        "personal loan from a friend",
        "loan provided by a friend",
        "loan i received from a friend",
        "funds from a loan provided by a friend",
        "from a friend",
    )),
    ("family_vague", ("help my family", "family there", "family support for")),
    ("medical", ("surgery", "hospital", "heart surgery", "medical center", "medical centre")),
)


def classify_text(text):
    folded = (text or "").lower()
    for label, needles in PATTERNS:
        for needle in needles:
            if needle in folded:
                return label
    return ""


# The agent may only add a suspicion. It cannot create a CLOSE.
AGENT_SUSPICIONS = (
    "structuring",
    "corridor_refusal",
    "mule",
    "third_party_use",
)
SPECIALIST_KEYS = (
    "document",
    "source_of_funds",
    "transaction_behaviour",
    "customer_profile",
    "counterparty",
    "internal_list",
    "country_risk",
)
_STATS_LOCK = threading.Lock()
STATS = {"calls": 0, "ok": 0}


def _agent_prompt(case):
    declared = (case["customer"].get("declared") or {})
    documents = []
    for doc in sorted(case["docs"], key=lambda item: item["doc_id"]):
        if doc["type"] in {"due_diligence_summary", "proof_of_address"}:
            continue
        text = doc["text"]
        if doc["type"] not in {
            "support_chat",
            "staff_note",
            "email",
            "customer_email",
            "customer_letter",
            "venue_email",
            "contact_note",
        }:
            text = text[:400]
        else:
            text = text[:1200]
        documents.append(
            {
                "doc_id": doc["doc_id"],
                "type": doc["type"],
                "title": doc["title"],
                "text": text,
            }
        )
    payments = []
    seen = set()
    for alert in case["alerts"]:
        for txn in case["flagged"].get(alert["alert_id"], []):
            if txn["transaction_id"] in seen:
                continue
            seen.add(txn["transaction_id"])
            payments.append(
                {
                    "transaction_id": txn["transaction_id"],
                    "booked_at": (txn.get("booked_at") or "")[:10],
                    "direction": txn.get("direction"),
                    "amount_gbp": txn.get("amount_gbp"),
                    "counterparty_name": txn.get("counterparty_name"),
                    "counterparty_country": txn.get("counterparty_country"),
                    "reference": txn.get("reference"),
                    "channel": txn.get("channel"),
                }
            )
            if len(payments) >= 16:
                break
        if len(payments) >= 16:
            break
    instructions = {
        "task": (
            "Investigate this case. Do not choose CLOSE, RFI or ESCALATE. "
            "Fill every specialist sentence from the documents and payments. "
            "Add a suspicion only when the customer's own words admit it, or a staff note records a refusal. "
            "Unusual amounts alone are not a suspicion. Leave suspicions empty when unsure."
        ),
        "allowed_suspicions": list(AGENT_SUSPICIONS),
        "suspicion_guide": {
            "mule": "The customer says they receive other people's money and pass it on.",
            "structuring": "The customer asks whether splitting or staying under a threshold avoids checks.",
            "corridor_refusal": "The customer will not explain a higher-risk country payment.",
            "third_party_use": "The customer says other people invest or move money through the account.",
        },
        "case": {
            "institution": case["institution"],
            "customer": customer_name(case),
            "business_activity": case["customer"].get("business_activity"),
            "expected_countries": declared.get("expected_countries") or [],
            "rules": list(case["rules"]),
            "alerts": [
                {"alert_id": alert["alert_id"], "rule_id": alert["rule_id"], "measured": alert.get("measured") or {}}
                for alert in case["alerts"]
            ],
        },
        "payments": payments,
        "documents": documents,
        "response_shape": {
            "suspicions": [],
            "specialists": {key: "one sentence" for key in SPECIALIST_KEYS},
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "You are the specialist review for one AML case. "
                "Reply with JSON only. Never output CLOSE, RFI or ESCALATE."
            ),
        },
        {"role": "user", "content": json.dumps(instructions, ensure_ascii=False, sort_keys=True)},
    ]


def classify_documents(docs, allow_model):
    del allow_model
    labels = {}
    for doc in docs:
        if doc["type"] == "contact_note":
            labels[doc["doc_id"]] = "benign"
            continue
        # Structured papers are read by the decision engine. A keyword in an
        # invoice or register must not become a suspicion label.
        if doc["type"] not in {
            "support_chat",
            "staff_note",
            "email",
            "customer_email",
            "customer_letter",
            "venue_email",
        }:
            labels[doc["doc_id"]] = ""
            continue
        label = classify_text(doc["text"])
        if label:
            labels[doc["doc_id"]] = label
        else:
            labels[doc["doc_id"]] = ""
    return labels


def review(case, allow_model):
    """Label chats from phrases, then always ask the specialist agent when a key is set."""
    labels = classify_documents(case["docs"], allow_model)
    for doc in case["docs"]:
        doc["label"] = labels.get(doc["doc_id"], "")
    case["agent_suspicions"] = []
    case["agent_called"] = False
    if not allow_model:
        return case
    parsed = llm.complete_json(_agent_prompt(case))
    case["agent_called"] = True
    flags = []
    if isinstance(parsed, dict):
        for item in parsed.get("suspicions") or []:
            if item in AGENT_SUSPICIONS and item not in flags:
                flags.append(item)
    flags.sort()
    case["agent_suspicions"] = flags
    with _STATS_LOCK:
        STATS["calls"] += 1
        if isinstance(parsed, dict):
            STATS["ok"] += 1
    return case
