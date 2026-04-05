"""MQ Guardian Platform — Web Engine with Agent Brain Integration.

Endpoints:
  POST /analyze        — Analyze AS-IS CSV, return topology metrics + DOT
  POST /transform      — Transform AS-IS → TARGET, return metrics + DOT + rules
  POST /agent/analyze  — Run full Agent Brain: analyze + transform + LLM explain
  POST /agent/query    — Ask the Agent Brain a question (chatbot)
  POST /explain        — Legacy explain endpoint (now uses Agent Brain)
  GET  /graph/as_is_dot   — Cached AS-IS DOT
  GET  /graph/target_dot  — Cached TARGET DOT
  GET  /health         — Health check
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

ART_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts"))
os.makedirs(ART_DIR, exist_ok=True)

LAST_ASIS_DOT = ""
LAST_TARGET_DOT = ""
LAST_CSV_PATH = ""
AGENT_STATE = None
STEADY_AGENT = None  # SteadyStateAgent instance


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


def _get_agent():
    """Lazy-init the Agent Brain (avoids slow LLM init on import)."""
    from agent_brain.graph import AgentBrainGraph
    return AgentBrainGraph()


def _get_steady_agent():
    """Lazy-init the Steady-State Agent."""
    global STEADY_AGENT
    if STEADY_AGENT is None:
        from agent_brain.steady_state import SteadyStateAgent
        STEADY_AGENT = SteadyStateAgent()
    return STEADY_AGENT


def app(environ, start_response):
    global LAST_ASIS_DOT, LAST_TARGET_DOT, LAST_CSV_PATH, AGENT_STATE

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

        # === ANALYZE (quick, no LLM) ===
        if path == "/analyze" and method == "POST":
            csv_path, err = _save_upload(environ)
            if err:
                status, headers, body = bad_request(err)
            else:
                LAST_CSV_PATH = csv_path
                metrics, extras = analyze_topology(csv_path)
                LAST_ASIS_DOT = edges_to_dot(extras.get("edges", []))
                status, headers, body = _json("200 OK", {
                    "dataset": os.path.basename(csv_path),
                    "topology_metrics": metrics,
                    "as_is_dot": LAST_ASIS_DOT,
                })

        # === TRANSFORM (quick, no LLM) ===
        elif path == "/transform" and method == "POST":
            csv_path, err = _save_upload(environ)
            if err and LAST_CSV_PATH and os.path.exists(LAST_CSV_PATH):
                csv_path = LAST_CSV_PATH
            elif err:
                status, headers, body = bad_request(err)
                start_response(status, headers)
                return body

            export_dir = os.path.join(ART_DIR, "exports")
            out = run_transform(csv_path, export_dir)
            if out.get("error"):
                status, headers, body = _json("400 Bad Request", out)
            else:
                LAST_TARGET_DOT = out.get("target_dot", "")
                rule_results = check_all_rules_from_dir(export_dir)
                out["rule_validation"] = rule_results
                status, headers, body = _json("200 OK", out)

        # === AGENT FULL ANALYSIS (with LLM) ===
        elif path == "/agent/analyze" and method == "POST":
            csv_path, err = _save_upload(environ)
            if err and LAST_CSV_PATH and os.path.exists(LAST_CSV_PATH):
                csv_path = LAST_CSV_PATH
            elif err:
                status, headers, body = bad_request(err)
                start_response(status, headers)
                return body

            try:
                agent = _get_agent()
                export_dir = os.path.join(ART_DIR, "exports")
                state = agent.run_bootstrap(csv_path, export_dir)
                AGENT_STATE = state

                # Also update DOT caches
                if state.as_is_metrics:
                    metrics, extras = analyze_topology(csv_path)
                    LAST_ASIS_DOT = edges_to_dot(extras.get("edges", []))
                if state.target_dot:
                    LAST_TARGET_DOT = state.target_dot

                # Rule validation
                rule_results = check_all_rules_from_dir(export_dir)

                response = {
                    "ok": True,
                    "as_is_metrics": state.as_is_metrics,
                    "target_metrics": state.target_metrics,
                    "as_is_dot": LAST_ASIS_DOT,
                    "target_dot": LAST_TARGET_DOT,
                    "explanation": state.explanation,
                    "decision_report": state.decision_report,
                    "impact_analysis": state.impact_analysis,
                    "anomalies": state.anomalies,
                    "rule_validation": rule_results,
                    "audit_log": state.audit_log,
                    "correlation_id": state.correlation_id,
                    "error": state.error,
                }
                status, headers, body = _json("200 OK", response)
            except Exception as e:
                status, headers, body = _json("500 Internal Server Error", {
                    "error": str(e),
                    "trace": traceback.format_exc(),
                })

        # === AGENT QUERY (chatbot) ===
        elif path == "/agent/query" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try:
                payload = json.loads(raw or b"{}")
            except Exception:
                payload = {}

            query = payload.get("query", "")
            if not query:
                status, headers, body = bad_request("No query provided")
            else:
                try:
                    agent = _get_agent()
                    answer = agent.handle_query(query, AGENT_STATE)
                    status, headers, body = _json("200 OK", {
                        "query": query,
                        "answer": answer,
                    })
                except Exception as e:
                    status, headers, body = _json("500 Internal Server Error", {
                        "error": str(e),
                    })

        # === EXPLAIN (now uses Agent Brain) ===
        elif path == "/explain" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try:
                payload = json.loads(raw or b"{}")
            except Exception:
                payload = {}

            if AGENT_STATE and AGENT_STATE.explanation:
                explanation = AGENT_STATE.explanation
            else:
                t = payload.get("target_metrics", {}) or {}
                hubs = t.get("top5_hub_qm", []) or []
                explanation = (
                    "Run the Agent Brain analysis first (click 'Run Agent Brain') "
                    "to get a full LLM-powered explanation.\n\n"
                    "Quick summary: We enforce 1 QM per App, producer remoteQ + xmitQ, "
                    "and deterministic channel pairs. "
                    f"Brittle apps={t.get('brittle_apps')}, TCS={t.get('tcs_score')}. "
                    "Top hubs: "
                    + ", ".join([f"{qm}({deg})" for qm, deg in hubs[:5]])
                    + "."
                )
            status, headers, body = _json("200 OK", {"explanation": explanation})

        # === GRAPH DOT ===
        elif path == "/graph/as_is_dot":
            status, headers, body = _json("200 OK", {"dot": LAST_ASIS_DOT or "digraph G {}"})

        elif path == "/graph/target_dot":
            status, headers, body = _json("200 OK", {"dot": LAST_TARGET_DOT or "digraph G {}"})

        # === ACCEPT/REJECT BOOTSTRAP ===
        elif path == "/agent/accept" and method == "POST":
            sa = _get_steady_agent()
            # Load target edges into the steady agent's graph store
            edges_csv = os.path.join(ART_DIR, "exports", "edges_target_v2.csv")
            if os.path.exists(edges_csv):
                sa.graph_store.load_edges_csv(edges_csv)
            # Also load channels for IaC generation
            channels_csv = os.path.join(ART_DIR, "exports", "channels.csv")
            result = sa.accept_bootstrap(sa.target_version or "v1")
            status, headers, body = _json("200 OK", {
                "ok": True,
                "event": result.get("event", {}),
                "iac_pipeline": result.get("iac_pipeline", {}),
                "status": sa.get_status(),
            })

        elif path == "/agent/reject" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try: payload = json.loads(raw or b"{}")
            except: payload = {}
            sa = _get_steady_agent()
            result = sa.reject_bootstrap(sa.target_version or "v1", payload.get("reason", ""))
            status, headers, body = _json("200 OK", {
                "ok": True, "event": result, "status": sa.get_status(),
            })

        # === DRIFT DETECTION ===
        elif path == "/agent/drift" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try: payload = json.loads(raw or b"{}")
            except: payload = {}
            sa = _get_steady_agent()
            drifts = sa.detect_drift(payload)
            status, headers, body = _json("200 OK", {
                "drifts": drifts, "count": len(drifts),
            })

        # === ONBOARDING (full enterprise flow) ===
        elif path == "/agent/onboard" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try: payload = json.loads(raw or b"{}")
            except: payload = {}

            sa = _get_steady_agent()
            # Load graph store if not already loaded
            edges_csv = os.path.join(ART_DIR, "exports", "edges_target_v2.csv")
            if os.path.exists(edges_csv) and len(sa.graph_store._nodes) == 0:
                sa.graph_store.load_edges_csv(edges_csv)

            proposal = sa.onboard_application(payload)

            # Generate IaC artifacts for this onboarding
            iac_result = None
            try:
                from iac_engine.pipeline import IaCPipeline
                pipeline = IaCPipeline(artifacts_dir=ART_DIR)
                onboard_topo = {
                    "edges": [
                        {"src": payload.get("app_id", ""), "dst": proposal["new_objects"]["queue_manager"], "relation": "writes_to"},
                    ],
                    "channels": proposal["new_objects"].get("channels", []),
                }
                iac_result = pipeline.execute_full_pipeline(onboard_topo, environment="prod")
            except Exception as e:
                iac_result = {"status": "failed", "error": str(e)}

            proposal["iac_pipeline"] = iac_result
            status, headers, body = _json("200 OK", proposal)

        # === APPROVE/REJECT PROPOSAL ===
        elif path == "/agent/approve" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try: payload = json.loads(raw or b"{}")
            except: payload = {}
            sa = _get_steady_agent()
            result = sa.approve_proposal(payload.get("proposal_id", ""), payload.get("actor", "operator"))
            status, headers, body = _json("200 OK", result)

        elif path == "/agent/reject_proposal" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            raw = environ["wsgi.input"].read(size) if size > 0 else b""
            try: payload = json.loads(raw or b"{}")
            except: payload = {}
            sa = _get_steady_agent()
            result = sa.reject_proposal(payload.get("proposal_id", ""), payload.get("actor", "operator"), payload.get("reason", ""))
            status, headers, body = _json("200 OK", result)

        # === AGENT STATUS ===
        elif path == "/agent/status":
            sa = _get_steady_agent()
            status, headers, body = _json("200 OK", sa.get_status())

        # === VIEW IAC ARTIFACTS ===
        elif path == "/agent/iac/terraform":
            tf_dir = os.path.join(ART_DIR, "terraform", "prod")
            files = {}
            if os.path.isdir(tf_dir):
                for fname in os.listdir(tf_dir):
                    fpath = os.path.join(tf_dir, fname)
                    if os.path.isfile(fpath):
                        with open(fpath, "r") as f:
                            files[fname] = f.read()
            status, headers, body = _json("200 OK", {"files": files})

        elif path == "/agent/iac/ansible":
            ansible_dir = os.path.join(ART_DIR, "ansible", "prod")
            files = {}
            if os.path.isdir(ansible_dir):
                for root, dirs, fnames in os.walk(ansible_dir):
                    for fname in fnames:
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, ansible_dir)
                        with open(fpath, "r") as f:
                            files[rel] = f.read()
            status, headers, body = _json("200 OK", {"files": files})

        # === KAFKA TOPICS STATUS ===
        elif path == "/agent/kafka/topics":
            from event_bus.schemas import KAFKA_TOPICS
            sa = _get_steady_agent()
            topic_data = {}
            for topic, desc in KAFKA_TOPICS.items():
                # Count messages in the in-memory producer
                msg_count = len(sa.producer._producer.produced_messages.get(topic, []))
                topic_data[topic] = {
                    "description": desc,
                    "message_count": msg_count,
                }
            status, headers, body = _json("200 OK", {"topics": topic_data})

        # === AUDIT TRAIL ===
        elif path == "/agent/audit":
            sa = _get_steady_agent()
            status, headers, body = _json("200 OK", {"events": sa.get_audit_trail()})

        # === PENDING PROPOSALS ===
        elif path == "/agent/proposals":
            sa = _get_steady_agent()
            status, headers, body = _json("200 OK", {"proposals": sa.get_pending_proposals()})

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
    with make_server("", port, app) as httpd:
        print(f"MQ Guardian Engine listening on http://127.0.0.1:{port}")
        print(f"Open chatbot/static/index.html in your browser")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
