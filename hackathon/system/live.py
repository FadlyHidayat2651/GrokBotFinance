#!/usr/bin/env python3
"""Local page that shows each verdict while the decision path is still running.

    python3 live.py [port]

The page is at http://127.0.0.1:8765/. A run writes one official JSONL per
institution, the file validate.py checks, and pushes each finished case in
cases.jsonl order. A dataset folder with expected.jsonl and score_test.py gets
scored with its own scorer when the run finishes.
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

config.load_dotenv()

from config import parse_policy
from loader import load_institution
from pipeline import allow_model, evaluate_case, iter_cases, worker_count

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "live.html")
OUT_DIR = os.path.join(tempfile.gettempdir(), "aml-live")
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
LABEL_FILES = {"expected": "expected.jsonl", "scorer": "score_test.py", "gold": "gold_output.jsonl"}
SEARCH_DEPTH = 3

RUNS = {}
LOCK = threading.Lock()
ACTIVE = {"run": None}
BUNDLES = {}
DATASETS = {}
SCORERS = {}


def _find_institutions(root):
    found = []
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirs, files in os.walk(root):
        if "cases.jsonl" in files and "POLICY.md" in files:
            found.append(dirpath)
            dirs[:] = []
        elif dirpath.count(os.sep) - base >= SEARCH_DEPTH:
            dirs[:] = []
        else:
            dirs[:] = sorted(name for name in dirs if not name.startswith("."))
    bases = [os.path.basename(path) for path in found]
    named = {}
    for path, name in zip(found, bases):
        if bases.count(name) > 1:
            name = os.path.relpath(path, root)
        named[name] = os.path.realpath(path)
    return dict(sorted(named.items()))


def _dataset(dataset_id, label, root):
    files = {key: os.path.join(root, name) for key, name in LABEL_FILES.items()}
    dataset = {
        "id": dataset_id,
        "label": label,
        "root": root,
        "institutions": _find_institutions(root),
    }
    dataset.update({key: path if os.path.isfile(path) else None for key, path in files.items()})
    return dataset


def register_dataset(path):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("give a folder path")
    root = os.path.realpath(os.path.expanduser(path.strip()))
    if not os.path.isdir(root):
        raise ValueError("not a folder: " + root)
    with LOCK:
        for dataset in DATASETS.values():
            if dataset["root"] == root:
                return dataset
    dataset = _dataset(hashlib.sha1(root.encode("utf-8")).hexdigest()[:10], os.path.basename(root) or root, root)
    if not dataset["institutions"]:
        raise ValueError(
            "no institution folder (one holding cases.jsonl and POLICY.md) within %d levels of %s"
            % (SEARCH_DEPTH, root)
        )
    with LOCK:
        return DATASETS.setdefault(dataset["id"], dataset)


def public_dataset(dataset):
    return {
        "id": dataset["id"],
        "label": dataset["label"],
        "root": dataset["root"],
        "institutions": list(dataset["institutions"]),
        "has_labels": dataset["expected"] is not None,
        "has_scorer": dataset["scorer"] is not None,
        "has_gold": dataset["gold"] is not None,
    }


def get_dataset(dataset_id):
    with LOCK:
        dataset = DATASETS.get(dataset_id or "builtin")
    if dataset is None:
        raise ValueError("unknown dataset %r" % (dataset_id,))
    return dataset


def get_folder(dataset, name):
    folder = dataset["institutions"].get(name)
    if folder is None:
        raise ValueError("unknown institution %r in dataset %s" % (name, dataset["id"]))
    return folder


def get_bundle(dataset_id, name):
    folder = get_folder(get_dataset(dataset_id), name)
    with LOCK:
        cached = BUNDLES.get(folder)
    if cached is not None:
        return cached
    bundle = load_institution(folder)
    with LOCK:
        BUNDLES[folder] = bundle
    return bundle


DATASETS["builtin"] = _dataset(
    "builtin", "built-in institutions", os.path.realpath(os.path.join(HERE, "..", "institutions"))
)


def _scorer(path):
    path = os.path.realpath(path)
    with LOCK:
        module = SCORERS.get(path)
    if module is not None:
        return module
    name = "dataset_scorer_" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with LOCK:
        return SCORERS.setdefault(path, module)


def _subset(rows, case_ids):
    if case_ids is None:
        return rows
    wanted = set(case_ids)
    return [row for row in rows if row.get("case_id") in wanted]


def score_run(dataset, output_paths, case_ids):
    """Score with the dataset's own score_test.py, the way its main() does.

    case_ids=None scores every expected row. Otherwise only the expected rows a
    run covered count, so a one-institution run is not charged for the rest.
    """
    try:
        if dataset["scorer"] is None:
            with open(dataset["expected"], encoding="utf-8") as fh:
                rows = _subset([json.loads(line) for line in fh if line.strip()], case_ids)
            return {
                "labels_only": True,
                "rows": [
                    {
                        "case_id": row.get("case_id"),
                        "expected_verdict": row.get("verdict"),
                        "expected_reason_code": row.get("reason_code"),
                        "rationale": row.get("rationale"),
                    }
                    for row in rows
                ],
            }
        module = _scorer(dataset["scorer"])
        expected_rows = _subset(module.read_jsonl(dataset["expected"]), case_ids)
        allowed, alert_ids = module.collect_ids(dataset["root"])
        outputs, _ = module.load_outputs(output_paths)
        rep = module.score(expected_rows, outputs, allowed, alert_ids)
        rationale = {row.get("case_id"): row.get("rationale") for row in expected_rows}
        for row in rep["rows"]:
            row["rationale"] = rationale.get(row["case_id"])
        return json.loads(json.dumps(rep, default=str))
    except (Exception, SystemExit) as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def find_evidence(bundle, ident):
    folder = os.path.realpath(bundle["folder"])
    for rows in bundle["evidence_by_customer"].values():
        for row in rows:
            if row.get("doc_id") != ident:
                continue
            path = os.path.realpath(os.path.join(folder, row["file"]))
            if os.path.commonpath([folder, path]) != folder:
                raise ValueError("evidence file is outside the institution folder")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            return {
                "id": ident,
                "kind": "document",
                "title": row.get("title"),
                "type": row.get("type"),
                "dated": row.get("dated"),
                "text": text,
            }
    if ident in bundle["txns_by_id"]:
        return {"id": ident, "kind": "transaction", "row": bundle["txns_by_id"][ident]}
    if ident in bundle["list_by_id"]:
        return {"id": ident, "kind": "list_entry", "row": bundle["list_by_id"][ident]}
    return None


class Run(object):
    def __init__(self, dataset, label, legs, events_path):
        self.id = uuid.uuid4().hex[:12]
        self.dataset = dataset
        self.label = label
        self.legs = legs
        self.events_path = events_path
        self.case_ids = []
        self.score = None
        self.cancel = threading.Event()
        self.cond = threading.Condition()
        self.events = []
        self.done = False
        self.cancelled = False
        self.error = None
        self.t0 = None
        self.t_first = None
        self.t_done = None
        self.count = 0

    def publish(self, event):
        event["run_id"] = self.id
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def finish(self, event):
        event["run_id"] = self.id
        with self.cond:
            self.t_done = event.get("t")
            self.events.append(event)
            self.done = True
            self.cond.notify_all()

    def public_legs(self):
        return [{"institution": leg["institution"], "output_path": leg["output_path"]} for leg in self.legs]

    def done_event(self):
        return {
            "kind": "done",
            "t": time.perf_counter() - self.t0 if self.t0 else 0,
            "t_first": self.t_first,
            "count": self.count,
            "cancelled": self.cancelled,
            "output_path": self.legs[0]["output_path"],
            "legs": self.public_legs(),
            "score": self.score,
            "error": self.error,
        }

    def snapshot(self):
        return {
            "run_id": self.id,
            "dataset": self.dataset["id"],
            "institution": self.label,
            "done": self.done,
            "cancelled": self.cancelled,
            "count": self.count,
            "t_first": self.t_first,
            "t_done": self.t_done,
            "output_path": self.legs[0]["output_path"],
            "legs": self.public_legs(),
            "events_path": self.events_path,
            "score": self.score,
            "error": self.error,
        }


def start_run(dataset, names, limit):
    os.makedirs(OUT_DIR, exist_ok=True)
    legs = [
        {
            "institution": name,
            "folder": dataset["institutions"][name],
            "output_path": os.path.join(OUT_DIR, uuid.uuid4().hex + ".jsonl"),
        }
        for name in names
    ]
    label = names[0] if len(names) == 1 else "*"
    run = Run(dataset, label, legs, os.path.join(OUT_DIR, uuid.uuid4().hex + ".events.jsonl"))
    with LOCK:
        if ACTIVE["run"] is not None:
            ACTIVE["run"].cancel.set()
        RUNS[run.id] = run
        ACTIVE["run"] = run
    threading.Thread(target=_execute, args=(run, limit), daemon=True).start()
    return run


def _execute(run, limit):
    run.t0 = time.perf_counter()
    try:
        enabled = allow_model()
        with open(run.events_path, "w", encoding="utf-8", buffering=1) as events:
            for leg in run.legs:
                if run.cancel.is_set():
                    break
                bundle = load_institution(leg["folder"])
                policy = parse_policy(bundle["policy_text"])
                cases = bundle["cases"][:limit] if limit else bundle["cases"]
                run.case_ids.extend(case["case_id"] for case in cases)
                with open(leg["output_path"], "w", encoding="utf-8", buffering=1) as official:
                    for line, view in iter_cases(
                        bundle, policy, cases, enabled, worker_count(enabled), run.cancel
                    ):
                        if run.cancel.is_set():
                            break
                        official.write(json.dumps(line, ensure_ascii=False) + "\n")
                        official.flush()
                        event = {
                            "kind": "case",
                            "dataset": run.dataset["id"],
                            "institution": leg["institution"],
                            "line": line,
                            "rules": view["rules"],
                            "payments": view["payments"],
                            "documents": view["documents"],
                            "agent": view["agent"],
                            "t": time.perf_counter() - run.t0,
                        }
                        events.write(json.dumps(event, ensure_ascii=False) + "\n")
                        events.flush()
                        if run.t_first is None:
                            run.t_first = event["t"]
                        run.count += 1
                        run.publish(event)
            run.cancelled = run.cancel.is_set()
            if run.dataset["expected"] and not run.cancelled:
                run.score = score_run(run.dataset, [leg["output_path"] for leg in run.legs], run.case_ids)
            done = run.done_event()
            events.write(json.dumps(done, ensure_ascii=False) + "\n")
            events.flush()
    except Exception as exc:
        run.error = str(exc)
        done = run.done_event()
    run.finish(done)


def _read_json(handler, limit):
    length = int(handler.headers.get("Content-Length") or "0")
    if length > limit:
        raise ValueError("body too large")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _place_upload(files):
    if not isinstance(files, dict) or not files:
        raise ValueError("upload a folder")
    if len(files) > 250:
        raise ValueError("folder has too many files")
    root = tempfile.mkdtemp(prefix="aml-upload-")
    total = 0
    for rel, text in files.items():
        if not isinstance(rel, str) or not isinstance(text, str):
            raise ValueError("bad file")
        total += len(text.encode("utf-8"))
        if total > 8_000_000:
            raise ValueError("folder is too large")
        parts = [part for part in rel.replace("\\", "/").split("/") if part and part != "."]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("bad path")
        dest = os.path.join(root, *parts)
        parent = os.path.dirname(dest)
        os.makedirs(parent, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
    found = _find_institutions(root)
    if not found:
        raise ValueError("folder needs cases.jsonl and POLICY.md")
    return min(found.values(), key=len)


def _upload_dataset(folder):
    return {
        "id": None,
        "label": "upload",
        "root": folder,
        "institutions": {"upload": folder},
        "expected": None,
        "scorer": None,
        "gold": None,
    }


def _arg(query, name, default=""):
    return (query.get(name) or [default])[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("live " + (fmt % args) + "\n")

    def _allowed(self, post):
        """Refuse other hosts (DNS rebinding) and non-JSON POSTs (cross-site forms skip CORS preflight)."""
        if urlsplit("//" + (self.headers.get("Host") or "")).hostname not in ALLOWED_HOSTS:
            self.close_connection = True
            self._json(403, {"error": "host not allowed"})
            return False
        content_type = (self.headers.get("Content-Type") or "").lower()
        if post and not content_type.startswith("application/json"):
            self.close_connection = True
            self._json(415, {"error": "POST needs Content-Type application/json"})
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if not self._allowed(False):
                return
            if parsed.path == "/":
                self._bytes(200, "text/html; charset=utf-8", open(PAGE, "rb").read())
            elif parsed.path == "/api/datasets":
                with LOCK:
                    datasets = list(DATASETS.values())
                self._json(200, [public_dataset(dataset) for dataset in datasets])
            elif parsed.path == "/api/cases":
                self._cases(query)
            elif parsed.path == "/api/evidence":
                self._evidence(query)
            elif parsed.path == "/api/status":
                self._status(query)
            elif parsed.path == "/api/events":
                self._events(query)
            elif parsed.path == "/api/tail":
                self._follow(query, lambda run: run.events_path)
            elif parsed.path == "/api/official":
                leg = int(_arg(query, "leg", "0"))
                self._follow(query, lambda run: run.legs[leg]["output_path"])
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:
            if not getattr(self, "_started", False):
                self._json(400, {"error": str(exc)})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if not self._allowed(True):
                return
            if parsed.path == "/api/run":
                body = _read_json(self, 64_000)
                dataset = get_dataset(body.get("dataset"))
                name = body.get("institution")
                if name != "*":
                    get_folder(dataset, name)
                names = list(dataset["institutions"]) if name == "*" else [name]
                limit = int(body["limit"]) if body.get("limit") else None
                run = start_run(dataset, names, limit)
                self._json(200, {
                    "run_id": run.id,
                    "dataset": dataset["id"],
                    "institution": run.label,
                    "output_path": run.legs[0]["output_path"],
                    "legs": run.public_legs(),
                })
            elif parsed.path == "/api/case":
                body = _read_json(self, 64_000)
                self._json(200, _one_case(body.get("dataset"), body.get("institution"), body.get("case_id")))
            elif parsed.path == "/api/datasets":
                body = _read_json(self, 64_000)
                self._json(200, public_dataset(register_dataset(body.get("path"))))
            elif parsed.path == "/api/score":
                body = _read_json(self, 64_000)
                dataset = get_dataset(body.get("dataset"))
                if body.get("source") != "gold":
                    raise ValueError("source must be gold")
                if dataset["expected"] is None or dataset["gold"] is None:
                    raise ValueError("dataset has no expected.jsonl or gold_output.jsonl")
                self._json(200, {"dataset": dataset["id"], "source": "gold", "score": score_run(dataset, [dataset["gold"]], None)})
            elif parsed.path == "/api/upload":
                body = _read_json(self, 8_000_000)
                folder = _place_upload(body.get("files") or {})
                run = start_run(_upload_dataset(folder), ["upload"], None)
                self._json(200, {"run_id": run.id, "institution": "upload", "output_path": run.legs[0]["output_path"], "folder": folder})
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:
            self.close_connection = True
            self._json(400, {"error": str(exc)})

    def _cases(self, query):
        dataset_id = _arg(query, "dataset") or "builtin"
        name = _arg(query, "institution")
        bundle = get_bundle(dataset_id, name)
        self._json(200, {
            "dataset": dataset_id,
            "institution": name,
            "case_ids": [case["case_id"] for case in bundle["cases"]],
        })

    def _evidence(self, query):
        bundle = get_bundle(_arg(query, "dataset") or "builtin", _arg(query, "institution"))
        found = find_evidence(bundle, _arg(query, "id"))
        if found is None:
            self._json(404, {"error": "no evidence with that id"})
        else:
            self._json(200, found)

    def _status(self, query):
        run = self._run(query)
        self._json(200, run.snapshot())

    def _run(self, query):
        run_id = _arg(query, "run_id")
        with LOCK:
            run = RUNS.get(run_id)
        if run is None:
            raise ValueError("unknown run")
        return run

    def _events(self, query):
        run = self._run(query)
        self._started = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
            index = 0
            while True:
                with run.cond:
                    while index >= len(run.events):
                        if run.done:
                            return
                        run.cond.wait(timeout=1)
                    ready = list(run.events[index:])
                    index = len(run.events)
                for event in ready:
                    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"data: " + payload + b"\n\n")
                    self.wfile.flush()
                    if event.get("kind") == "done":
                        return
        except (BrokenPipeError, ConnectionResetError):
            return

    def _follow(self, query, path_of):
        run = self._run(query)
        path = path_of(run)
        self._started = True
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            deadline = time.time() + 120
            while not os.path.exists(path):
                if run.done or time.time() > deadline:
                    break
                time.sleep(0.01)
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as fh:
                while True:
                    line = fh.readline()
                    if line:
                        self.wfile.write(line.encode("utf-8"))
                        self.wfile.flush()
                        continue
                    if run.done:
                        extra = fh.read()
                        if extra:
                            self.wfile.write(extra.encode("utf-8"))
                            self.wfile.flush()
                        break
                    time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _json(self, status, payload):
        self._started = True
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, status, content_type, body):
        self._started = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _one_case(dataset_id, name, case_id):
    dataset_id = dataset_id or "builtin"
    bundle = get_bundle(dataset_id, name)
    policy = parse_policy(bundle["policy_text"])
    case = next((item for item in bundle["cases"] if item["case_id"] == case_id), None)
    if case is None:
        raise ValueError("unknown case")
    line, view = evaluate_case(bundle, policy, case, allow_model())
    return {
        "kind": "case",
        "run_id": "case",
        "dataset": dataset_id,
        "institution": name,
        "line": line,
        "rules": view["rules"],
        "payments": view["payments"],
        "documents": view["documents"],
        "agent": view["agent"],
    }


def serve(port):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    sys.stderr.write("live page http://127.0.0.1:%d/\n" % port)
    server.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
