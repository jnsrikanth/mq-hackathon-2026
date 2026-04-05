#!/usr/bin/env python3
"""MQ Guardian Platform — End-to-End Demo Script.

Runs the full Bootstrap workflow:
  1. Ingest AS-IS CSV
  2. Analyze topology metrics
  3. Transform to TARGET state
  4. Load into graph store
  5. Detect anomalies
  6. Compute impact analysis
  7. Generate decision report
  8. Explain with LLM (Ollama locally, Tachyon in production)

Usage:
  python demo.py <path-to-csv>
  python demo.py  (uses sample data if no CSV provided)

Environment variables for Tachyon (corporate network):
  LLM_PROVIDER=tachyon
  TACHYON_API_KEY=<key>
  TACHYON_BASE_URL=<url>
  TACHYON_MODEL=<model>
"""

import json
import os
import sys
import tempfile

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def create_sample_csv() -> str:
    """Create a small sample CSV for demo purposes."""
    content = """\
Discrete Queue Name,ProducerName,ConsumerName,PrimaryAppRole,queue_manager_name,app_id,line_of_business,q_type,remote_q_mgr_name,remote_q_name,xmit_q_name,Neighborhood
8A.OK.VFHVIGL.HDF.RQST,DFCeth - HUVY MIPKNVL ZYU,OOIKN JIU,Producer,WL6EX2C,8A,TECHCT,Remote,WQ26,8A.OK.VFHVIGL.HDF.RQST.XA21,OK.WQ26,Mainframe
8A.OK.VFHVIGL.HDF.RQST,DFCeth - HUVY MIPKNVL ZYU,OOIKN JIU,Consumer,WL6EX2C,OK,TECHCT,Remote,WQ26,8A.OK.VFHVIGL.HDF.RQST.XA21,OK.WQ26,Mainframe
8AFK.PPCSM.ZEEWALA.ACK,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Consumer,WL6ER2D,PPCSM,TECHCCIBT,Local,,,,Wholesale Banking
8AFK.PPCSM.ZEEWALA.ACK,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Producer,WL6ER2D,8AFK,TECHCCIBT,Local,,,,Wholesale Banking
8AFK.PPCSM.ZEEWALA.DAT,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Consumer,WL6ER2D,PPCSM,TECHCCIBT,Local,,,,Wholesale Banking
8AFK.PPCSM.ZEEWALA.DAT,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Producer,WL6ER2D,8AFK,TECHCCIBT,Local,,,,Wholesale Banking
8AFK.PPCSM.ARCNE.ACK,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Consumer,WL6ER2D,PPCSM,TECHCCIBT,Local,,,,Wholesale Banking
8AFK.PPCSM.ARCNE.ACK,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Producer,WL6ER2D,8AFK,TECHCCIBT,Local,,,,Wholesale Banking
"""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="mq_demo_")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def main():
    print("=" * 70)
    print("  MQ Guardian Platform — Intelligent Agent Demo")
    print("=" * 70)
    print()

    # Determine CSV path
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        csv_path = sys.argv[1]
        print(f"📄 Using provided CSV: {csv_path}")
    else:
        csv_path = create_sample_csv()
        print(f"📄 Using sample CSV: {csv_path}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
    os.makedirs(out_dir, exist_ok=True)

    # Check LLM provider
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    model = os.environ.get("LLM_MODEL", "qwen2.5-coder:7b")
    print(f"🤖 LLM Provider: {provider} | Model: {model}")
    print()

    # Import and run
    from agent_brain.graph import AgentBrainGraph

    print("🚀 Starting Bootstrap workflow...")
    print("-" * 50)

    agent = AgentBrainGraph()
    state = agent.run_bootstrap(csv_path, out_dir)

    # Display results
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)

    if state.error:
        print(f"\n❌ Error: {state.error}")
    else:
        print(f"\n✅ Bootstrap completed successfully")

    print(f"\n📊 AS-IS Metrics:")
    if state.as_is_metrics:
        for k, v in state.as_is_metrics.items():
            if k != "top5_hub_qm":
                print(f"   {k}: {v}")
            else:
                print(f"   top5_hub_qm: {v[:3]}...")

    print(f"\n🎯 TARGET Metrics:")
    if state.target_metrics:
        for k, v in state.target_metrics.items():
            if k != "top5_hub_qm":
                print(f"   {k}: {v}")
            else:
                print(f"   top5_hub_qm: {v[:3]}...")

    if state.as_is_metrics and state.target_metrics:
        as_edges = state.as_is_metrics.get("total_edges", 0)
        tgt_edges = state.target_metrics.get("total_edges", 0)
        as_tcs = state.as_is_metrics.get("tcs_score", 0)
        tgt_tcs = state.target_metrics.get("tcs_score", 0)
        print(f"\n📉 Improvement:")
        print(f"   Edges: {as_edges} → {tgt_edges} ({as_edges - tgt_edges} reduced)")
        print(f"   TCS:   {as_tcs} → {tgt_tcs} ({as_tcs - tgt_tcs} reduced)")
        print(f"   Brittle apps: {state.as_is_metrics.get('brittle_apps', '?')} → {state.target_metrics.get('brittle_apps', 0)}")

    print(f"\n⚠️  Anomalies: {len(state.anomalies)}")
    for a in state.anomalies[:3]:
        print(f"   - [{a.get('severity', '?')}] {a.get('description', '')[:80]}")

    if state.impact_analysis:
        print(f"\n💥 Impact Analysis:")
        print(f"   Risk score: {state.impact_analysis.get('risk_score', 'N/A')}")
        hubs = state.impact_analysis.get("top_hubs", [])
        if hubs:
            print(f"   Top hubs: {hubs[:3]}")

    if state.decision_report:
        print(f"\n📋 Decision Report:")
        print(f"   Recommendation: {state.decision_report.get('recommendation', 'N/A')[:80]}")

    print(f"\n🧠 Agent Explanation (LLM):")
    print("-" * 50)
    if state.explanation:
        # Print first 500 chars
        print(state.explanation[:500])
        if len(state.explanation) > 500:
            print(f"... ({len(state.explanation)} chars total)")
    else:
        print("   (no explanation generated)")

    print(f"\n📁 Artifacts:")
    if state.iac_artifacts:
        for k, v in state.iac_artifacts.items():
            print(f"   {k}: {v}")

    # ================================================================
    # RULE VALIDATION WITH EVIDENCE
    # ================================================================
    from transformer.rule_checker import check_all_rules_from_dir, print_rule_results

    rule_results = check_all_rules_from_dir(out_dir)
    print_rule_results(rule_results)

    print(f"📝 Audit trail: {len(state.audit_log)} events")
    print()
    print("=" * 70)
    print("  Demo complete. Run the Web UI for visual comparison:")
    print(f"  python -m chatbot.serve_engine")
    print(f"  Then open chatbot/static/index.html in your browser")
    print("=" * 70)


if __name__ == "__main__":
    main()
