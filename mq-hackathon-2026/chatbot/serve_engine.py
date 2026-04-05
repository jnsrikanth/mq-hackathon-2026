"""Pure-Python WSGI web engine for the MQ Guardian dashboard.

Endpoints:
  POST /analyze      — Analyze AS-IS CSV, return topology metrics + DOT
  POST /transform    — Transform AS-IS → TARGET, return metrics + DOT + rules
  POST /explain      — Explain target topology decisions
  GET  /graph/as_is_dot — Return cached AS-IS DOT graph
  GET  /graph/target_dot — Return cached TARGET DOT graph
  POST /stream/push  — Queue a CSV for streaming analysis
  GET  /stream/metrics — Return current stream metrics
  GET  /health       — Health check
"""

from __future__ import annotations

import cgi
import json
import os
import threading
import time
import traceback
from wsgiref.simple_server import make_server

from transformer.analyzer import analyze_topology, edges_to_dot
from transformer.transform import run_transform
from transformer.rule_checker import check_all_rules_from_dir
from chatbot.stream_shim import StreamState

ART_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts"))
os.makedirs(ART_DIR, exist_ok=True)

STATE = StreamState(ART_DIR)
LAST_ASIS_DOT = ""
LAST_TARGET_DOT = ""
LAST_CSV_PATH = ""  # remember last uploaded CSV for transform reuse


def _cors():
    return [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
    ]


def _json(status, payload):
    body = json.dumps(payload, indent=2, default=str).encode("utf-8")
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ] + _cors()
    return status, headers, [body]


def bad_request(msg):
    return _json("400 Bad Request", {"error": msg})


def _save_upload(environ):
    """Save uploaded file to artifacts dir. Returns (filepath, error)."""
    try:
        form = cgi.FieldStorage(fp=environ.get("wsgi.input"), environ=environ)
        fileitem = form["file"] if form and "file" in form else None
        if fileitem is None:
            return None, "No file uploaded"
        filename = getattr(fileitem, "filename", "") or ""
        if not filename.lower().endswith(".csv"):
            return None, "Upload a .csv file"
        data = fileitem.file.read()
        if not data:
            return None, "Empty file"
        tmp = os.path.join(ART_DIR, f"upload_{int(time.time())}.csv")
        with open(tmp, "wb") as f:
            f.write(data)
        return tmp, None
    except Exception as e:
        return None, f"Upload failed: {e}"


def app(environ, start_response):
    global LAST_ASIS_DOT, LAST_TARGET_DOT, LAST_CSV_PATH

    try:
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET")

        if method == "OPTIONS":
            start_response("204 No Content", _cors())
            return [b""]

        if path == "/health":
            status, headers, body = _json("200 OK", {"ok": True})
            start_response(status, headers)
            return body

        # === ANALYZE ===
        if path == "/analyze" and method == "POST":
            csv_path, err = _save_upload(environ)
            if err:
                status, headers, body = bad_request(err)
            else:
                LAST_CSV_PATH = csv_path
                metrics, extras = analyze_topology(csv_path)
                LAST_ASIS_DOT = edges_to_dot(extras.get("edges", []))
                STATE.set_as_is(metrics)
                status, headers, body = _json("200 OK", {
                    "dataset": os.path.basename(csv_path),
                    "topology_metrics": metrics,
                    "as_is_dot": LAST_ASIS_DOT,
                })

        # === TRANSFORM ===
        elif path == "/transform" and method == "POST":
            csv_path, err = _save_upload(environ)
            if err:
                # Try reusing last uploaded CSV
                if LAST_CSV_PATH and os.path.exists(LAST_CSV_PATH):
                    csv_path = LAST_CSV_PATH
                    err = None
                else:
                    status, headers, body = bad_request(err)
                    start_response(status, headers)
                    return body

            try:
                export_dir = os.path.join(ART_DIR, "exports")
                out = run_transform(csv_path, export_dir)
                if out.get("error"):
                    status, headers, body = _json("400 Bad Request", out)
                else:
                    STATE.set_target(out.get("target_report", {}))
                    LAST_TARGET_DOT = out.get("target_dot", "")

                    # Run rule validation
                    rule_results = check_all_rules_from_dir(export_dir)
                    out["rule_validation"] = rule_results

                    status, headers, body = _json("200 OK", out)
            except Exception as e:
                status, headers, body = _json("500 Internal Server Error", {
                    "error": str(e),
                    "trace": traceback.format_exc(),
                })

        # === EXPLAIN ===
        elif path == "/explain" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try:
                payload = json.loads(raw or b"{}")
            except Exception:
                payload = {}
            t = payload.get("target_metrics", {}) or {}
            hubs = t.get("top5_hub_qm", []) or []
            msg = (
                "We enforce 1 QM per App, producer remoteQ + xmitQ, "
                "and deterministic channel pairs. "
                f"Brittle apps={t.get('brittle_apps')}, TCS={t.get('tcs_score')}. "
                "Top hubs: "
                + ", ".join([f"{qm}({deg})" for qm, deg in hubs[:5]])
                + "."
            )
            status, headers, body = _json("200 OK", {"explanation": msg})

        # === GRAPH DOT ===
        elif path == "/graph/as_is_dot":
            status, headers, body = _json(
                "200 OK", {"dot": LAST_ASIS_DOT or "digraph G {}"}
            )

        elif path == "/graph/target_dot":
            status, headers, body = _json(
                "200 OK", {"dot": LAST_TARGET_DOT or "digraph G {}"}
            )

        # === STREAMING ===
        elif path == "/stream/push" and method == "POST":
            csv_path, err = _save_upload(environ)
            if err:
                status, headers, body = bad_request(err)
            else:
                status, headers, body = _json(
                    "200 OK", {"queued": os.path.basename(csv_path)}
                )

        elif path == "/stream/metrics":
            status, headers, body = _json("200 OK", STATE.get_stream_metrics())

        else:
            status, headers, body = _json("404 Not Found", {"error": "no route"})

        start_response(status, headers)
        return body

    except Exception as e:
        status, headers, body = _json("500 Internal Server Error", {
            "error": str(e),
            "trace": traceback.format_exc(),
        })
        start_response(status, headers)
        return body


def main(port: int = 8088) -> None:
    t = threading.Thread(target=STATE.watch_folder, daemon=True)
    t.start()
    with make_server("", port, app) as httpd:
        print(f"Engine listening on http://127.0.0.1:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
