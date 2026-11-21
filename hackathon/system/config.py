"""Runtime settings. Thresholds ship in policy_config.json and are checked against POLICY.md."""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "policy_config.json")

with open(CONFIG_PATH, encoding="utf-8") as fh:
    CONFIG = json.load(fh)

CLOSE_CODES = set(CONFIG["close_codes"])
RFI_CODES = set(CONFIG["rfi_codes"])
ESCALATE_CODES = set(CONFIG["escalate_codes"])
CODES_FOR = {
    "CLOSE": CLOSE_CODES,
    "RFI": RFI_CODES,
    "ESCALATE": ESCALATE_CODES,
}

FILLER_DOC_TYPES = {"due_diligence_summary", "proof_of_address", "contact_note"}
FREE_TEXT_TYPES = {
    "support_chat",
    "contact_note",
    "staff_note",
    "email",
    "customer_email",
    "customer_letter",
    "reverification_record",
    "venue_email",
    "insurer_letter",
    "solicitor_letter",
    "sending_bank_fraud_report",
    "gift_letter",
    "settlement_letter",
    "deposit_return_letter",
    "holding_deposit_receipt",
    "redundancy_letter",
    "employment_contract",
    "cancellation_confirmation",
    "factoring_agreement",
    "supply_contract",
}


def load_dotenv():
    """Fill missing environment variables from system/.env. Existing variables win."""
    path = os.path.join(HERE, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_policy(policy_text):
    """Read the settings the SOP states in words. JSON is the packaged copy."""
    countries = list(CONFIG["high_risk_countries"])
    match = re.search(
        r"Higher-risk countries as of[^:]*:\s*(.+?)\.",
        policy_text,
        flags=re.IGNORECASE,
    )
    if match:
        parsed = [part.strip() for part in match.group(1).split(",") if part.strip()]
        if len(parsed) >= 10:
            countries = parsed
    invoice_min = None
    if re.search(r"Payments of £10,000 or more", policy_text):
        invoice_min = float(CONFIG["payments_fx_invoice_min_gbp"])
    own_multiple = float(CONFIG["own_account_profile_multiple"])
    if not re.search(r"5 times the declared income or volume", policy_text):
        own_multiple = float(CONFIG["own_account_profile_multiple"])
    return {
        "high_risk_countries": countries,
        "own_account_profile_multiple": own_multiple,
        "invoice_release_gbp": invoice_min,
    }


def sanity_check_policy(policy_text):
    """Compare the packaged country list and the 5x setting with this folder's SOP."""
    notes = []
    packaged = CONFIG["high_risk_countries"]
    parsed = parse_policy(policy_text)["high_risk_countries"]
    if [c.lower() for c in packaged] != [c.lower() for c in parsed]:
        missing = [c for c in packaged if c.lower() not in policy_text.lower()]
        extra = [c for c in parsed if c not in packaged]
        notes.append(
            "high-risk list differs from policy_config.json: "
            "missing_in_policy=%s extra_in_policy=%s" % (missing, extra)
        )
    for country in packaged:
        if country.lower() not in policy_text.lower():
            notes.append("packaged country not in POLICY.md: %s" % country)
    if "5 times the declared income or volume" not in policy_text:
        notes.append("POLICY.md does not state the 5 times own-account setting")
    return notes
