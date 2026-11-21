#!/usr/bin/env python3
"""Local page that shows each verdict while the decision path is still running.

    python3 live.py [port]

The page is at http://127.0.0.1:8765/. A run writes an official JSONL, the file
validate.py checks, and pushes each finished case in cases.jsonl order.
"""

import json
import os
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

config.load_dotenv()

from config import parse_policy
from loader import load_institution
from pipeline import allow_model, evaluate_case, iter_cases, worker_count

HERE = os.path.dirname(os.path.abspath(__file__))
INSTITUTIONS = os.path.abspath(os.path.join(HERE, "..", "institutions"))
NAMES = ("bank", "emoney", "investment", "payments_fx")
PAGE = os.path.join(HERE, "live.html")
OUT_DIR = os.path.join(tempfile.gettempdir(), "aml-live")

RUNS = {}
LOCK = threading.Lock()
ACTIVE = {"run": None}
BUNDLES = {}


def get_bundle(name):
    if name not in NAMES:
        raise ValueError("unknown institution")
    folder = os.path.join(INSTITUTIONS, name)
    with LOCK:
        cached = BUNDLES.get(folder)
    if cached is not None:
        return cached
    bundle = load_institution(folder)
    with LOCK:
        BUNDLES[folder] = bundle
    return bundle


class Run(object):
    def __init__(self, label, output_path, events_path):
        self.id = uuid.uuid4().hex[:12]
        self.label = label
        self.output_path = output_path
        self.events_path = events_path
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

    def snapshot(self):
        return {
            "run_id": self.id,
            "institution": self.label,
            "done": self.done,
            "cancelled": self.cancelled,
            "count": self.count,
            "t_first": self.t_first,
            "t_done": self.t_done,
            "output_path": self.output_path,
            "events_path": self.events_path,
            "error": self.error,
        }


def start_run(folder, label, limit):
    os.makedirs(OUT_DIR, exist_ok=True)
    run = Run(
        label,
        os.path.join(OUT_DIR, uuid.uuid4().hex + ".jsonl"),
        os.path.join(OUT_DIR, uuid.uuid4().hex + ".events.jsonl"),
    )
    with LOCK:
        if ACTIVE["run"] is not None:
            ACTIVE["run"].cancel.set()
        RUNS[run.id] = run
        ACTIVE["run"] = run
    threading.Thread(target=_execute, args=(run, folder, limit), daemon=True).start()
    return run


def _execute(run, folder, limit):
    run.t0 = time.perf_counter()
    try:
        bundle = load_institution(folder)
        policy = parse_policy(bundle["policy_text"])
        cases = bundle["cases"]
        if limit:
            cases = cases[: int(limit)]
        enabled = allow_model()
        with open(run.output_path, "w", encoding="utf-8", buffering=1) as official, open(
            run.events_path, "w", encoding="utf-8", buffering=1
        ) as events:
            for line, view in iter_cases(
                bundle, policy, cases, enabled, worker_count(enabled), run.cancel
            ):
                if run.cancel.is_set():
                    run.cancelled = True
                    break
                official.write(json.dumps(line, ensure_ascii=False) + "\n")
                official.flush()
                event = {
                    "kind": "case",
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
            done = {
                "kind": "done",
                "t": time.perf_counter() - run.t0,
                "t_first": run.t_first,
                "count": run.count,
                "cancelled": run.cancelled,
                "output_path": run.output_path,
            }
            events.write(json.dumps(done, ensure_ascii=False) + "\n")
            events.flush()
    except Exception as exc:
        run.error = str(exc)
        done = {
            "kind": "done",
            "t": time.perf_counter() - run.t0 if run.t0 else 0,
            "t_first": run.t_first,
            "count": run.count,
            "cancelled": run.cancelled,
            "error": run.error,
        }
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
    found = []
    for dirpath, _dirs, names in os.walk(root):
        if "cases.jsonl" in names and "POLICY.md" in names:
            found.append(dirpath)
    if not found:
        raise ValueError("folder needs cases.jsonl and POLICY.md")
    found.sort(key=len)
    return found[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("live " + (fmt % args) + "\n")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._bytes(200, "text/html; charset=utf-8", open(PAGE, "rb").read())
            elif parsed.path == "/api/cases":
                self._cases(query)
            elif parsed.path == "/api/status":
                self._status(query)
            elif parsed.path == "/api/events":
                self._events(query)
            elif parsed.path == "/api/tail":
                self._follow(query, "events_path")
            elif parsed.path == "/api/official":
                self._follow(query, "output_path")
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:
            if not getattr(self, "_started", False):
                self._json(400, {"error": str(exc)})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/run":
                body = _read_json(self, 64_000)
                name = body.get("institution")
                if name not in NAMES:
                    raise ValueError("unknown institution")
                limit = body.get("limit")
                run = start_run(os.path.join(INSTITUTIONS, name), name, limit)
                self._json(200, {"run_id": run.id, "institution": name, "output_path": run.output_path})
            elif parsed.path == "/api/case":
                body = _read_json(self, 64_000)
                self._json(200, _one_case(body.get("institution"), body.get("case_id")))
            elif parsed.path == "/api/upload":
                body = _read_json(self, 8_000_000)
                folder = _place_upload(body.get("files") or {})
                run = start_run(folder, "upload", None)
                self._json(200, {"run_id": run.id, "institution": "upload", "output_path": run.output_path, "folder": folder})
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:
            self._json(400, {"error": str(exc)})

    def _cases(self, query):
        name = (query.get("institution") or [""])[0]
        bundle = get_bundle(name)
        self._json(200, {"institution": name, "case_ids": [case["case_id"] for case in bundle["cases"]]})

    def _status(self, query):
        run = self._run(query)
        self._json(200, run.snapshot())

    def _run(self, query):
        run_id = (query.get("run_id") or [""])[0]
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

    def _follow(self, query, attr):
        run = self._run(query)
        path = getattr(run, attr)
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


def _one_case(name, case_id):
    bundle = get_bundle(name)
    policy = parse_policy(bundle["policy_text"])
    case = next((item for item in bundle["cases"] if item["case_id"] == case_id), None)
    if case is None:
        raise ValueError("unknown case")
    line, view = evaluate_case(bundle, policy, case, allow_model())
    return {
        "kind": "case",
        "run_id": "case",
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
