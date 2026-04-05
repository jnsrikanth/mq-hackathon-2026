"""Pure-Python WSGI web engine for the MQ Guardian dashboard.

Endpoints:
  POST /analyze      — Analyze AS-IS CSV, return topology metrics
  POST /transform    — Transform AS-IS → TARGET, return metrics + DOT
  POST /explain      — Explain target topology decisions
  GET  /graph/as_is_dot — Return cached AS-IS DOT graph
  POST /stream/push  — Queue a CSV for streaming analysis
  GET  /stream/metrics — Return current stream metrics
  GET  /health       — Health check

Uses only Python stdlib (wsgiref) — no Flask, FastAPI, or other frameworks.
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
from chatbot.stream_shim import StreamState

ART_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts"))
os.makedirs(ART_DIR, exist_ok=True)

STATE = StreamState(ART_DIR)
LAST_ASIS_DOT = ""


def _cors() -> list[tuple[str, str]]:
    return [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
    ]


def _json(
    status: str, payload: dict,
) -> tuple[str, list[tuple[str, str]], list[bytes]]:
    body = json.dumps(payload, indent=2).encode("utf-8")
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ] + _cors()
    return status, headers, [body]


def bad_request(msg: str) -> tuple[str, list[tuple[str, str]], list[bytes]]:
    return _json("400 Bad Request", {"error": msg})


def _read_fieldstorage(environ: dict) -> tuple[object | None, str]:
    """Parse multipart form upload. Returns (fileitem, filename) or (None, error_msg)."""
    try:
        form = cgi.FieldStorage(fp=environ.get("wsgi.input"), environ=environ)
        fileitem = form["file"] if form and "file" in form else None
        filename = getattr(fileitem, "filename", "") or ""
        return fileitem, filename
    except Exception as e:
        return None, f"Failed to parse multipart form: {e}"


def app(environ: dict, start_response: object) -> list[bytes]:
    """WSGI application."""
    global LAST_ASIS_DOT

    try:
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET")

        if method == "OPTIONS":
            start_response("204 No Content", _cors())  # type: ignore[arg-type]
            return [b""]

        if path == "/health":
            status, headers, body = _json("200 OK", {"ok": True})
            start_response(status, headers)  # type: ignore[arg-type]
            return body

        # === ANALYZE ===
        if path == "/analyze" and method == "POST":
            result = _read_fieldstorage(environ)
            fileitem, filename = result
            if fileitem is None or not str(filename).lower().endswith(".csv"):
                status, headers, body = bad_request("upload a .csv")
            else:
                data = fileitem.file.read()  # type: ignore[union-attr]
                if not data:
                    status, headers, body = bad_request("empty file")
                else:
                    tmp = os.path.join(ART_DIR, f"as_is_{int(time.time())}.csv")
                    with open(tmp, "wb") as f:
                        f.write(data)
                    metrics, extras = analyze_topology(tmp)
                    LAST_ASIS_DOT = edges_to_dot(extras.get("edges", []))
                    STATE.set_as_is(metrics)
                    status, headers, body = _json("200 OK", {
                        "dataset": os.path.basename(tmp),
                        "topology_metrics": metrics,
                    })

        # === TRANSFORM ===
        elif path == "/transform" and method == "POST":
            result = _read_fieldstorage(environ)
            fileitem, filename = result
            if fileitem is None or not str(filename).lower().endswith(".csv"):
                status, headers, body = bad_request("upload a .csv")
            else:
                data = fileitem.file.read()  # type: ignore[union-attr]
                if not data:
                    status, headers, body = bad_request("empty file")
                else:
                    tmp = os.path.join(ART_DIR, f"as_is_{int(time.time())}.csv")
                    with open(tmp, "wb") as f:
                        f.write(data)
                    out = run_transform(tmp, os.path.join(ART_DIR, "exports"))
                    if out.get("error"):
                        status, headers, body = _json("400 Bad Request", out)
                    else:
                        STATE.set_target(out.get("target_report", {}))
                        # Run rule validation with evidence
                        from transformer.rule_checker import check_all_rules_from_dir
                        rule_results = check_all_rules_from_dir(
                            os.path.join(ART_DIR, "exports")
                        )
                        out["rule_validation"] = rule_results
                        status, headers, body = _json("200 OK", out)

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

        # === STREAMING ===
        elif path == "/stream/push" and method == "POST":
            result = _read_fieldstorage(environ)
            fileitem, filename = result
            if fileitem is None or not str(filename).lower().endswith(".csv"):
                status, headers, body = bad_request("upload a .csv")
            else:
                p = STATE.push_stream_csv(fileitem)
                status, headers, body = _json(
                    "200 OK", {"queued": os.path.basename(p)}
                )

        elif path == "/stream/metrics":
            status, headers, body = _json("200 OK", STATE.get_stream_metrics())

        else:
            status, headers, body = _json("404 Not Found", {"error": "no route"})

        start_response(status, headers)  # type: ignore[arg-type]
        return body

    except Exception as e:
        status, headers, body = _json("500 Internal Server Error", {
            "error": str(e),
            "trace": traceback.format_exc(),
        })
        start_response(status, headers)  # type: ignore[arg-type]
        return body


def main(port: int = 8088) -> None:
    """Start the WSGI server."""
    t = threading.Thread(target=STATE.watch_folder, daemon=True)
    t.start()
    with make_server("", port, app) as httpd:
        print(f"Engine listening on http://127.0.0.1:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
