# LLM Pipeline Generator

A hosted internal tool that turns a pipeline spec into generated Scala/DDL/tests
in a per-job workspace. The agent's loop is plan → validate-DAG → generate →
validate (compile + conventions) → run tests → **SUCCEEDED**. The loop ends
there — PR creation is intentionally manual: open the workspace,
`git commit && git push`, and raise the PR yourself.

This repo is split into five packages:

| Package | What it does |
|---|---|
| `agent-worker` | Orchestrator + agent + tools (the brain). Runs as a CLI or behind the API. |
| `agent-conventions` | Offline tree-sitter extractor that produces `conventions.json` for a target repo. |
| `agent-eval` | Eval harness: case loader, grader, runner that drives the worker. |
| `agent-api` | FastAPI server. Wraps the worker for the frontend. SSE event stream. |
| `agent-frontend` | React + Vite UI. JobsList + JobDetail with live event tape. |

For design context see `HLD.md` and `LLD.md`.

---

## Prerequisites

- Python 3.11+
- Node 18+ (for the frontend)
- An Anthropic or Gemini API key — or skip and use mock scripts

---

## Setup (one-time)

All commands run from the repo root (`pipeline_automation/`).

### 1. Create the project venv

A single venv at `pipeline_automation/.venv` drives everything — worker, API,
conventions, eval. The four packages are installed editable so code edits take
effect immediately, no reinstall needed.

```sh
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

That installs all four Python packages with `[dev]` extras (pytest, ruff, mypy).

### 2. Install frontend deps

```sh
cd agent-frontend
npm install
cd ..
```

### 3. Configure env

```sh
cp .env.example .env
# Open .env and fill in either ANTHROPIC_API_KEY (for Claude)
# or AGENT_WORKER_GEMINI_API_KEY (for Gemini).
# If you don't have a key handy, leave them empty and use a mock script (below).
```

The `.env` lives at the repo root and is read by both the worker and the API.

---

## Activate the venv

In every terminal you use for the project:

```sh
source .venv/bin/activate
```

After activation, all four CLIs are on PATH:

| CLI | What it does |
|---|---|
| `agent-api` | Start the FastAPI server |
| `agent-worker generate ...` | Run the worker against a spec without the API |
| `agent-conventions extract ...` | Run the tree-sitter extractor over a target repo |
| `agent-eval run ...` | Run the eval harness |

Deactivate with `deactivate` when you're done.

---

## Run

### Two terminals

```sh
# Terminal 1 — API on :8000
source .venv/bin/activate
agent-api

# Terminal 2 — Frontend on :5173 (proxies /api → :8000)
cd agent-frontend
npm run dev
```

Open **http://localhost:5173** in your browser.

### One-liner (background)

```sh
source .venv/bin/activate
( agent-api & )
( cd agent-frontend && npm run dev & )
```

Stop both:

```sh
lsof -ti:8000 -ti:5173 | xargs kill
```

---

## Driving a job

In the UI, click **New job**. You have two options:

### Mode 1 — mock LLM (no API key needed)

Paste this absolute path into the **Mock script path** field:

```
<repo-root>/agent-eval/dataset/case_001_simple_workflow_node/mock_responses.json
```

Submit. The job runs against scripted responses, generates two stub Scala files,
passes validation, and lands SUCCEEDED. Useful for testing the UI / API without
burning real tokens.

### Mode 2 — real Claude or Gemini

Leave **Mock script path** empty. The API routes to whichever provider is set in
`.env` via `AGENT_API_LLM_PROVIDER`. Default is `claude`.

To switch providers, edit `.env` and restart the API:

```env
AGENT_API_LLM_PROVIDER=gemini   # or claude (default)
```

Model and effort are also env-tunable (see `.env.example`):

```env
AGENT_WORKER_CLAUDE_MODEL=claude-sonnet-4-6
AGENT_WORKER_CLAUDE_EFFORT=medium
```

---

## Worker CLI (no API needed)

The worker is also usable directly from the shell — useful for the eval harness or
local debugging. Same code path as the API.

```sh
source .venv/bin/activate

agent-worker generate \
  --spec agent-eval/dataset/case_001_simple_workflow_node/spec.yaml \
  --workspace /tmp/agent-test \
  --mock-script agent-eval/dataset/case_001_simple_workflow_node/mock_responses.json \
  --json-output

# Outputs:
#   - generated files in /tmp/agent-test/
#   - /tmp/agent-test/job_journal.json with the full event tape
```

Drop `--mock-script` to use real Claude/Gemini per `.env`.

---

## Conventions extractor

Produces the `conventions.json` artifact that the agent uses to match repo style.

```sh
source .venv/bin/activate

agent-conventions extract \
  --repo /path/to/pipelines-reports \
  --out /tmp/conventions.json
```

Then plug it into a worker run with `--conventions /tmp/conventions.json`, or into
the API request body via `"conventions_path"`.

---

## Eval harness

Runs the worker against the dataset and grades its output.

```sh
source .venv/bin/activate

agent-eval run case_001_simple_workflow_node \
  --output-root /tmp/eval-out \
  --report-json /tmp/eval-out/suite.json
```

Each case folder has its own `mock_responses.json`, so this works without API keys.

---

## Tests

With the venv activated:

```sh
# All four suites in one shot (~305 tests total)
pytest agent-worker/tests \
       agent-conventions/tests \
       agent-eval/tests \
       agent-api/tests
```

Or run them individually:

```sh
pytest agent-worker/tests        # ~261 tests (unit + integration)
pytest agent-conventions/tests   # ~18 tests
pytest agent-eval/tests          # ~17 tests
pytest agent-api/tests           # ~9 tests
```

---

## What the agent does today

```
INIT → PLANNING → OPTIMIZING_PLAN → GENERATING → VALIDATING → TESTING → SUCCEEDED
                                         ↑           ↓            ↓
                                         └── REPAIRING ←──────────┘
                                              (≤3 validation, ≤2 test attempts)
```

- **Planner** outputs a structured Plan (file list with `depends_on`, descriptions, assumptions).
- **Optimizing-plan pass** (deterministic, no LLM): validates the file DAG —
  catches cycles, dangling deps, duplicate paths up-front — and topologically
  sorts `plan.files` so upstream nodes are generated before their dependents.
  Bad DAGs fail fast with a `dag.invalid` event instead of wasting LLM calls.
- **Generator** writes each file via `write_file`. One LLM call per file (plus
  tool round-trips). Already-generated upstream file bodies are appended to
  the per-file user prompt so the LLM sees the actual contracts of its
  dependencies, not just their plan descriptions.
- **Validator** runs `scalafmt`, `scalac`, and 5 text-based convention rules.
- **Test stage** runs `sbt` for pure-Scala specs (devbox/SSH stubbed).
- On VALIDATING/TESTING failure, the agent loops back through REPAIRING.
- Stops at SUCCEEDED. **PR creation is manual** — open the workspace,
  `git commit && git push`, raise the PR yourself.

Every LLM call and tool invocation is captured in a per-job journal:

- `<workspace>/job_journal.json` — full event tape
- `/api/jobs/{id}/events/replay` — same tape via JSON API
- `/api/jobs/{id}/events` — live SSE stream the frontend consumes

---

## Troubleshooting

**`agent-api: command not found`** — activate the venv first: `source .venv/bin/activate`.

**Real LLM call fails with auth error** — make sure the right key is set in `.env`:
- Claude: `ANTHROPIC_API_KEY` (or `AGENT_WORKER_CLAUDE_API_KEY`)
- Gemini: `AGENT_WORKER_GEMINI_API_KEY`, plus `AGENT_API_LLM_PROVIDER=gemini`

**`.env` changes aren't picked up** — restart the API after editing `.env`. Settings
are loaded once at startup.

**Job stuck in `running`** — check the API stderr for tracebacks; the orchestrator
runs in a thread pool, so exceptions land there.

**Frontend can't reach the API** — the Vite dev server proxies `/api/*` to
`:8000`; make sure the API is up and listening on that port.

**"skills_root does not exist"** — usually fine without explicit config; the API
auto-locates the worker's `skills/` folder. Set `AGENT_API_SKILLS_ROOT` in
`.env` only if auto-detection picks the wrong path.
