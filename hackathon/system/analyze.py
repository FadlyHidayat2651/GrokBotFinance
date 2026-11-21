"""Classify free-text documents. Rules first. One model call only when a text is still unknown.

The model returns labels and specialist notes. It does not return a verdict.
"""

import json

import llm

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


# The model may only add a suspicion. It cannot create a CLOSE. Refunds, gifts and
# own-account letters are read from the document itself.
MODEL_LABELS = {
    "structuring",
    "corridor_refusal",
    "mule",
    "third_party_use",
    "friend_loan",
    "gift_story",
    "sale_story",
    "family_vague",
}


def _needs_model(doc):
    if doc["type"] not in {"support_chat", "staff_note"}:
        return False
    return classify_text(doc["text"]) == ""


def _prompt(docs):
    packed = []
    for doc in docs:
        packed.append(
            {
                "doc_id": doc["doc_id"],
                "type": doc["type"],
                "text": doc["text"][:2500],
            }
        )
    instructions = {
        "task": (
            "Label each document. Do not choose CLOSE, RFI or ESCALATE. "
            "Use only these labels: " + ", ".join(LABELS) + ", unknown."
        ),
        "label_guide": {
            "mule": "Customer says they receive other people's money and pass it on, often to crypto, for a job found online.",
            "third_party_use": "Customer says friends, cousins or other people invest or move money through the account.",
            "structuring": "Customer asks whether splitting or staying under a threshold avoids checks.",
            "corridor_refusal": "Customer will not explain a higher-risk country payment.",
            "returned_cash": "Customer says the cash is their own earlier withdrawal paid back in.",
            "sale_story": "Customer says the money is from selling a vehicle or other asset.",
            "gift_story": "Customer says the money is a gift from a relative.",
            "friend_loan": "Customer says a friend lent the money.",
            "family_vague": "Customer says only that a payment is family support, without a document.",
            "medical": "Payment is for a named person's medical treatment.",
            "benign": "Routine service chat or a story that matches none of the above.",
            "unknown": "The text does not support a label.",
        },
        "documents": packed,
        "response_shape": {
            "documents": [{"doc_id": "DOC", "label": "benign"}],
            "specialists": {
                "document": "one sentence",
                "source_of_funds": "one sentence",
                "transaction_behaviour": "one sentence",
                "customer_profile": "one sentence",
                "counterparty": "one sentence",
                "internal_list": "one sentence",
                "country_risk": "one sentence",
            },
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "You extract labels from AML case documents. "
                "Reply with JSON only. Never output a verdict."
            ),
        },
        {"role": "user", "content": json.dumps(instructions, ensure_ascii=False, sort_keys=True)},
    ]


def classify_documents(docs, allow_model):
    labels = {}
    unknown = []
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
        elif _needs_model(doc):
            unknown.append(doc)
        else:
            labels[doc["doc_id"]] = ""
    if unknown and allow_model:
        parsed = llm.complete_json(_prompt(unknown))
        returned = {}
        if isinstance(parsed, dict):
            for row in parsed.get("documents") or []:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("label") or "")
                if label in MODEL_LABELS:
                    returned[row.get("doc_id")] = label
        for doc in unknown:
            labels[doc["doc_id"]] = returned.get(doc["doc_id"], "")
    else:
        for doc in unknown:
            labels[doc["doc_id"]] = ""
    return labels
