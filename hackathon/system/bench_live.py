#!/usr/bin/env python3
"""Measure the live page transports and check the decision output."""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "institutions"))
FIELDS = ("case_id", "alert_ids", "verdict", "reason_code", "reason", "evidence")


def tail_run(folder, out_path, limit):
    if os.path.exists(out_path):
        os.remove(out_path)
    env = os.environ.copy()
    env["DISABLE_LLM"] = "1"
    env.pop("CASE_LIMIT", None)
    if limit:
        env["CASE_LIMIT"] = str(limit)
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "run"), folder, out_path],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    while not os.path.exists(out_path):
        if proc.poll() is not None:
            break
        time.sleep(0.002)
    first = None
    count = 0
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as fh:
            while True:
                line = fh.readline()
                if line:
                    count += 1
                    if first is None:
                        first = time.perf_counter() - t0
                    continue
                if proc.poll() is not None:
                    extra = fh.read()
                    if extra:
                        count += extra.count("\n")
                        if first is None:
                            first = time.perf_counter() - t0
                    break
                time.sleep(0.005)
    stderr = proc.stderr.read().decode("utf-8", "replace")
    code = proc.wait()
    exit_t = time.perf_counter() - t0
    return {
        "first": first,
        "exit": exit_t,
        "count": count,
        "before_exit": first is not None and first < exit_t,
        "code": code,
        "stderr": stderr.strip().splitlines()[-1] if stderr.strip() else "",
    }


def post(port, path, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path)) as response:
        return json.loads(response.read().decode("utf-8"))


def read_first(port, path):
    t0 = time.perf_counter()
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
    first = None
    kind = None
    with urllib.request.urlopen(request) as response:
        for raw in response:
            text = raw.decode("utf-8").strip()
            if text.startswith("data:"):
                text = text[5:].strip()
            if not text or text.startswith(":"):
                continue
            if first is None:
                first = time.perf_counter() - t0
                try:
                    kind = json.loads(text).get("kind") or "line"
                except json.JSONDecodeError:
                    kind = "line"
            if '"kind": "done"' in text or '"kind":"done"' in text:
                break
    return {"client_first": first, "client_done": time.perf_counter() - t0, "first_kind": kind}


def time_server(port, institution, limit, transport):
    started = post(port, "/api/run", {"institution": institution, "limit": limit})
    path = "/api/events" if transport == "sse" else "/api/" + transport
    client = read_first(port, path + "?run_id=" + started["run_id"])
    status = get_json(port, "/api/status?run_id=" + started["run_id"])
    client["server_t_first"] = status.get("t_first")
    client["server_t_done"] = status.get("t_done")
    client["count"] = status.get("count")
    client["before_exit"] = (
        status.get("t_first") is not None
        and status.get("t_done") is not None
        and status["t_first"] < status["t_done"]
        and client["client_first"] is not None
        and client["client_first"] < client["client_done"]
    )
    client["output_path"] = status.get("output_path")
    client["run_id"] = started["run_id"]
    return client


def lines(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def same_fields(left, right):
    if len(left) != len(right):
        return False, "count %d vs %d" % (len(left), len(right))
    for index, (one, two) in enumerate(zip(left, right)):
        for field in FIELDS:
            if one.get(field) != two.get(field):
                return False, "%s field %s" % (one.get("case_id"), field)
    return True, "match"


def validate(folder, path):
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "validate.py"), folder, path],
        capture_output=True,
        text=True,
    )
    return {"code": proc.returncode, "out": proc.stdout.strip()}


def minimal_emoney():
    folder = os.path.join(ROOT, "emoney")
    case_id = "EM-CS100735"
    customer_id = None
    files = {}
    cases = []
    with open(os.path.join(folder, "cases.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["case_id"] == case_id:
                cases.append(row)
                customer_id = row["customer_id"]
    alerts = []
    with open(os.path.join(folder, "alerts.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["case_id"] == case_id:
                alerts.append(row)
    customers = []
    with open(os.path.join(folder, "customers.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["customer_id"] == customer_id:
                customers.append(row)
    txns = []
    with open(os.path.join(folder, "transactions.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["customer_id"] == customer_id:
                txns.append(row)
    evidence = []
    with open(os.path.join(folder, "evidence.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["customer_id"] == customer_id:
                evidence.append(row)
                with open(os.path.join(folder, row["file"]), encoding="utf-8") as handle:
                    files["pack/" + row["file"]] = handle.read()
    reviews = []
    with open(os.path.join(folder, "previous_reviews.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("customer_id") == customer_id:
                reviews.append(row)
    files["pack/cases.jsonl"] = "".join(json.dumps(row) + "\n" for row in cases)
    files["pack/alerts.jsonl"] = "".join(json.dumps(row) + "\n" for row in alerts)
    files["pack/customers.jsonl"] = "".join(json.dumps(row) + "\n" for row in customers)
    files["pack/transactions.jsonl"] = "".join(json.dumps(row) + "\n" for row in txns)
    files["pack/evidence.jsonl"] = "".join(json.dumps(row) + "\n" for row in evidence)
    files["pack/previous_reviews.jsonl"] = "".join(json.dumps(row) + "\n" for row in reviews)
    files["pack/internal_list.jsonl"] = ""
    with open(os.path.join(folder, "POLICY.md"), encoding="utf-8") as fh:
        files["pack/POLICY.md"] = fh.read()
    return files


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    bank = os.path.join(ROOT, "bank")
    report = {}
    report["tail_200"] = tail_run(bank, "/tmp/aml-tail-200.jsonl", 200)
    report["tail_1000"] = tail_run(bank, "/tmp/aml-tail-1000.jsonl", None)
    report["sse_200"] = time_server(port, "bank", 200, "sse")
    report["tail_events_200"] = time_server(port, "bank", 200, "tail")
    report["official_200"] = time_server(port, "bank", 200, "official")
    report["sse_1000"] = time_server(port, "bank", None, "sse")
    report["run_vs_server"] = same_fields(lines("/tmp/aml-tail-1000.jsonl"), lines(report["sse_1000"]["output_path"]))
    report["validate_run"] = validate(bank, "/tmp/aml-tail-1000.jsonl")
    report["validate_server"] = validate(bank, report["sse_1000"]["output_path"])
    uploaded = post(port, "/api/upload", {"files": minimal_emoney()})
    deadline = time.time() + 30
    status = {}
    while time.time() < deadline:
        status = get_json(port, "/api/status?run_id=" + uploaded["run_id"])
        if status.get("done"):
            break
        time.sleep(0.05)
    report["upload_status"] = status
    report["validate_upload"] = validate(uploaded["folder"], status["output_path"])
    case = post(port, "/api/case", {"institution": "emoney", "case_id": "EM-CS100735"})
    upload_line = lines(status["output_path"])[0]
    report["case_line"] = {field: case["line"][field] for field in FIELDS}
    report["case_agent"] = case["agent"]
    report["case_documents"] = case["documents"]
    report["case_payments"] = case["payments"]
    report["case_rules"] = case["rules"]
    report["upload_line"] = {field: upload_line[field] for field in FIELDS}
    report["case_matches_upload"] = report["case_line"] == report["upload_line"]
    report["agent"] = agent_pair()
    print(json.dumps(report, indent=2))
    ok = (
        report["tail_200"]["before_exit"]
        and report["tail_1000"]["before_exit"]
        and report["sse_200"]["before_exit"]
        and report["sse_1000"]["before_exit"]
        and report["tail_events_200"]["before_exit"]
        and report["official_200"]["before_exit"]
        and report["run_vs_server"][0]
        and report["validate_run"]["code"] == 0
        and report["validate_server"]["code"] == 0
        and report["validate_upload"]["code"] == 0
        and report["case_matches_upload"]
        and report["agent"]["ok"]
    )
    return 0 if ok else 1


def agent_pair():
    os.environ.pop("DISABLE_LLM", None)
    sys.path.insert(0, HERE)
    from config import parse_policy
    from live import _place_upload
    from loader import load_institution
    from pipeline import evaluate_case

    bundle = load_institution(os.path.join(ROOT, "emoney"))
    policy = parse_policy(bundle["policy_text"])
    case = next(item for item in bundle["cases"] if item["case_id"] == "EM-CS100735")
    line, view = evaluate_case(bundle, policy, case, True)
    folder = _place_upload(minimal_emoney())
    uploaded = load_institution(folder)
    line2, view2 = evaluate_case(uploaded, parse_policy(uploaded["policy_text"]), uploaded["cases"][0], True)
    out_path = os.path.join(folder, "out.jsonl")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line2, ensure_ascii=False) + "\n")
    checked = validate(folder, out_path)
    expected = {
        "verdict": "CLOSE",
        "reason_code": "DOCUMENTED_EVENT",
        "evidence": ["EM-T941243048", "INS-1600"],
    }
    return {
        "ok": (
            view["agent"] == "ran"
            and view2["agent"] == "ran"
            and not _suspicions_changed(line, expected)
            and not _suspicions_changed(line2, expected)
            and {field: line[field] for field in FIELDS} == {field: line2[field] for field in FIELDS}
            and checked["code"] == 0
        ),
        "case_agent": view["agent"],
        "upload_agent": view2["agent"],
        "case": {field: line[field] for field in ("verdict", "reason_code", "evidence")},
        "upload": {field: line2[field] for field in ("verdict", "reason_code", "evidence")},
        "validate": checked,
    }


def _suspicions_changed(line, expected):
    return any(line.get(field) != value for field, value in expected.items())


if __name__ == "__main__":
    sys.exit(main())
