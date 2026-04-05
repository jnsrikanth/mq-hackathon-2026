# MQ Guardian Platform — Corporate Laptop Setup Guide

## Offline Install (Corporate Network — No PyPI Access)

This repo vendors all Python wheels for fully offline installation.

```bash
# 1. Clone the repo
git clone https://github.com/jnsrikanth/mq-hackathon-2026.git
cd mq-hackathon-2026

# 2. Create Python virtual environment (Python 3.12)
python -m venv .venv

# 3. Activate it
# Windows (Git Bash):
source .venv/Scripts/activate
# Windows (CMD):
.venv\Scripts\activate.bat
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# 4. Install dependencies OFFLINE from vendored wheels
pip install --no-index --find-links=./wheels -r requirements.txt

# 5. Start the web engine
python -m chatbot.serve_engine

# 6. Open the dashboard
# Double-click chatbot/static/index.html in Explorer/Finder
# Or: start chatbot/static/index.html  (Windows)
# Or: open chatbot/static/index.html   (macOS)
```

You should see:
```
MQ Guardian Engine listening on http://127.0.0.1:8088
```

The dashboard will show "Engine connected" when the server is running.

---

## LLM Configuration

The Agent Brain supports two LLM backends:

### Option A: Local Ollama (personal laptop / offline demo)

```bash
# Install Ollama (if not already installed)
# https://ollama.com/download

# Start Ollama
ollama serve

# Pull a model (7B fits in 24GB RAM)
ollama pull qwen2.5-coder:7b

# Start the engine (Ollama is the default)
python -m chatbot.serve_engine
```

No environment variables needed — Ollama on localhost:11434 is the default.

### Option B: Tachyon Studio (corporate network)

Set these environment variables BEFORE starting the engine:

```bash
# Windows (Git Bash):
export LLM_PROVIDER=tachyon
export TACHYON_API_KEY=<your-tachyon-api-key>
export TACHYON_BASE_URL=<your-tachyon-endpoint-url>
export TACHYON_MODEL=<preferred-model-name>

# Windows (CMD):
set LLM_PROVIDER=tachyon
set TACHYON_API_KEY=<your-tachyon-api-key>
set TACHYON_BASE_URL=<your-tachyon-endpoint-url>
set TACHYON_MODEL=<preferred-model-name>

# Windows (PowerShell):
$env:LLM_PROVIDER="tachyon"
$env:TACHYON_API_KEY="<your-tachyon-api-key>"
$env:TACHYON_BASE_URL="<your-tachyon-endpoint-url>"
$env:TACHYON_MODEL="<preferred-model-name>"

# Then start the engine
python -m chatbot.serve_engine
```

The `langchain-openai` wheel is already vendored — no additional install needed.

### Test Tachyon Connection

Before running the full demo, verify the Tachyon connection:

```bash
# Set env vars first (see Option B above), then:
python -c "from agent_brain.llm_config import test_tachyon_connection; print(test_tachyon_connection())"
```

Expected output on success:
```json
{"status": "success", "model": "gpt-4-turbo", "response": "Hello from MQ Guardian Agent..."}
```

### Option C: No LLM (fallback mode)

If neither Ollama nor Tachyon is available, the system still works with template-based responses:

```bash
# Just start the engine — it will use FallbackLLM automatically
python -m chatbot.serve_engine
```

The Analyze, Transform, Rule Validation, and IaC Pipeline all work without an LLM. Only the Agent Brain explanation and chatbot responses will be template-based instead of AI-generated.

---

## Demo Walkthrough

### Step 1: Upload and Analyze
1. Open `chatbot/static/index.html` in Chrome
2. Click "Choose File" and select `data/as_is_topology.csv`
3. Click **📊 Analyze** — see AS-IS metrics and topology graph

### Step 2: Run Agent Brain
4. Click **🧠 Run Agent Brain** — wait 30-60 seconds for LLM
5. See: KPI cards, charts, topology graphs, LLM analysis, decision report, impact analysis, blast radius, rule validation (all 8 rules)

### Step 3: Accept Target Topology
6. Click **✅ Accept** in the approval bar
7. See: IaC pipeline stages (Terraform + Ansible), Kafka topics with message counts, agent status

### Step 4: Onboard New Application
8. Scroll to "Application Onboarding Wizard"
9. Fill in: App Name, App ID, Role, LOB, Neighborhood, Partners
10. Click **🚀 Submit Onboarding Request**
11. See: Assigned QM, queue routing chain, policy validation, IaC pipeline
12. Click **✅ Approve Onboarding**

### Step 5: Check Drift
13. In the chatbot, click **🔍 Check Drift**
14. See drift detection results

### Step 6: Ask the Agent
15. Type questions or use preset prompts in the chatbot

### CLI Demo (alternative)
```bash
python demo.py data/as_is_topology.csv
```

---

## Using Your Real 12K-Row Dataset

Replace the sample data with your corporate CSV:

```bash
# Copy your real CSV into the data folder
cp /path/to/your/real-dataset.csv data/as_is_topology.csv

# Run the CLI demo
python demo.py data/as_is_topology.csv

# Or use the Web UI — upload the CSV through the browser
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError` | Run `pip install --no-index --find-links=./wheels -r requirements.txt` |
| `No matching distribution found` | Wheels are for Python 3.12 + Windows x64. Check `python --version` |
| `Cannot reach engine at localhost:8088` | Make sure `python -m chatbot.serve_engine` is running |
| Ollama connection refused | Run `ollama serve` in a separate terminal (personal laptop only) |
| Tachyon auth error | Check `TACHYON_API_KEY` and `TACHYON_BASE_URL` env vars |
| Agent Brain takes too long | Normal — LLM inference takes 30-60s |
| Buttons don't respond | Hard refresh: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac) |
| `pip` not found | Use `python -m pip` instead of `pip` |
| Windows path issues | Use forward slashes or Git Bash |

---

## Architecture Summary

```
Corporate Laptop                          Tachyon Studio
┌─────────────────────────────────┐      ┌──────────────┐
│  Browser (index.html)           │      │  LLM API     │
│  ├── Upload CSV                 │      │  (OpenAI     │
│  ├── Charts / Graphs / KPIs    │      │   compatible) │
│  ├── Agent Chatbot              │      └──────┬───────┘
│  ├── Onboarding Wizard          │             │
│  └── HiTL Approve/Reject       │             │
│           │                     │             │
│  Python WSGI Engine (:8088)     │             │
│  ├── Transformer (CSV→Target)   │             │
│  ├── Policy Engine (8 rules)    │             │
│  ├── Decision Engine            │◄────────────┘
│  ├── Impact Analysis            │   TACHYON_API_KEY
│  ├── Agent Brain (LangGraph)    │
│  ├── Graph Store (in-memory)    │
│  ├── Event Bus (in-memory)      │
│  ├── IaC Pipeline               │
│  │   ├── Terraform Generator    │
│  │   └── Ansible Generator      │
│  └── Steady-State Agent         │
│      ├── Drift Detection        │
│      └── Onboarding Flow        │
└─────────────────────────────────┘
```
