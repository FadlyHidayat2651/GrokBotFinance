"""One case through the existing decision path, yielded in cases.jsonl order."""

import os
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import config

config.load_dotenv()

from analyze import review
from config import CONFIG
from decide import decide
from explain import render
from facts import build_case, gbp


def allow_model():
    if os.environ.get("DISABLE_LLM") == "1":
        return False
    return bool(os.environ.get("XAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


def worker_count(enabled):
    if not enabled:
        return 1
    return max(1, int(CONFIG["max_workers"]))


def evaluate_case(bundle, policy, case, enabled):
    """Return the official JSON line and the display fields that sit beside it."""
    try:
        built = build_case(bundle, policy, case, {})
        review(built, enabled)
        line = render(built, decide(built))
        return line, view_for(built, line)
    except Exception:
        sys.stderr.write("case %s failed\n%s\n" % (case.get("case_id"), traceback.format_exc()))
        line = {
            "case_id": case.get("case_id"),
            "alert_ids": list(case.get("alert_ids") or []),
            "verdict": "ESCALATE",
            "reason_code": "ESC_UNEXPLAINED_FUNDS",
            "reason": (
                "This case could not be completed from the files. "
                "The activity is escalated because it is unexplained."
            ),
            "evidence": [],
        }
        return line, {
            "rules": [],
            "payments": [],
            "documents": [],
            "agent": "failed",
        }


def view_for(built, line):
    evidence = set(line.get("evidence") or [])
    payments = []
    seen = set()
    flagged = []
    for alert in built["alerts"]:
        for txn in built["flagged"].get(alert["alert_id"], []):
            if txn["transaction_id"] in seen:
                continue
            seen.add(txn["transaction_id"])
            flagged.append(txn)
    cited = [txn for txn in flagged if txn["transaction_id"] in evidence]
    chosen = cited or flagged
    for txn in chosen:
        payments.append(
            {
                "transaction_id": txn["transaction_id"],
                "amount_gbp": txn.get("amount_gbp"),
                "amount": gbp(txn["amount_gbp"]) if txn.get("amount_gbp") is not None else "",
                "counterparty_name": txn.get("counterparty_name") or "",
                "booked_at": (txn.get("booked_at") or "")[:10],
            }
        )
    by_id = {doc["doc_id"]: doc for doc in built["docs"]}
    documents = []
    for item in line.get("evidence") or []:
        doc = by_id.get(item)
        if doc:
            documents.append({"doc_id": doc["doc_id"], "title": doc.get("title") or ""})
    return {
        "rules": [
            {"alert_id": alert["alert_id"], "rule_id": alert["rule_id"]}
            for alert in built["alerts"]
        ],
        "payments": payments,
        "documents": documents,
        "agent": built.get("agent_status") or "skipped",
    }


def iter_cases(bundle, policy, cases, enabled, workers, cancel=None):
    """Yield (line, view) in case order as soon as the next case is ready."""
    if workers <= 1 or len(cases) <= 1:
        for case in cases:
            if cancel is not None and cancel.is_set():
                return
            yield evaluate_case(bundle, policy, case, enabled)
        return

    pending = {}
    lock = threading.Lock()
    ready = threading.Condition(lock)

    def run_one(index, case):
        if cancel is not None and cancel.is_set():
            result = None
        else:
            result = evaluate_case(bundle, policy, case, enabled)
        with ready:
            pending[index] = result
            ready.notify_all()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for index, case in enumerate(cases):
            futures.append(pool.submit(run_one, index, case))
        next_index = 0
        total = len(cases)
        while next_index < total:
            with ready:
                while next_index not in pending:
                    ready.wait()
                result = pending.pop(next_index)
            if cancel is not None and cancel.is_set():
                return
            if result is not None:
                yield result
            next_index += 1
        for future in futures:
            future.result()
