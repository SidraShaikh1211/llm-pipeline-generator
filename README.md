# Saturn Agent

A hosted internal tool that turns a pipeline spec into a real PR against a Saturn repo.
Plan → generate Scala/DDL/tests → validate → run tests → SUCCEEDED. PR is raised
manually after the agent finishes.

This repo is split into five packages:

| Package | What it does |
|---|---|
| `saturn-agent-worker` | Orchestrator + agent + tools (the brain). Runs as a CLI or behind the API. |
| `saturn-agent-conventions` | Offline tree-sitter extractor that produces `conventions.json` for a target repo. |
| `saturn-agent-eval` | Eval harness: case loader, grader, runner that drives the worker. |
| `saturn-agent-api` | FastAPI server. Wraps the worker for the frontend. SSE event stream. |
| `saturn-agent-frontend` | React + Vite UI. JobsList + JobDetail with live event tape. |

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
cd saturn-agent-frontend
npm install
cd ..
```

### 3. Configure env

```sh
cp .env.example .env
# Open .env and fill in either ANTHROPIC_API_KEY (for Claude)
# or SATURN_AGENT_GEMINI_API_KEY (for Gemini).
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
| `saturn-api` | Start the FastAPI server |
| `saturn-agent generate ...` | Run the worker against a spec without the API |
| `saturn-conventions extract ...` | Run the tree-sitter extractor over a target repo |
| `saturn-eval run ...` | Run the eval harness |

Deactivate with `deactivate` when you're done.

---

## Run

### Two terminals

```sh
# Terminal 1 — API on :8000
source .venv/bin/activate
saturn-api

# Terminal 2 — Frontend on :5173 (proxies /api → :8000)
cd saturn-agent-frontend
npm run dev
```

Open **http://localhost:5173** in your browser.

### One-liner (background)

```sh
source .venv/bin/activate
( saturn-api & )
( cd saturn-agent-frontend && npm run dev & )
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
<repo-root>/saturn-agent-eval/dataset/case_001_simple_workflow_node/mock_responses.json
```

Submit. The job runs against scripted responses, generates two stub Scala files,
passes validation, and lands SUCCEEDED. Useful for testing the UI / API without
burning real tokens.

### Mode 2 — real Claude or Gemini

Leave **Mock script path** empty. The API routes to whichever provider is set in
`.env` via `SATURN_API_LLM_PROVIDER`. Default is `claude`.

To switch providers, edit `.env` and restart the API:

```env
SATURN_API_LLM_PROVIDER=gemini   # or claude (default)
```

Model and effort are also env-tunable (see `.env.example`):

```env
SATURN_AGENT_CLAUDE_MODEL=claude-sonnet-4-6
SATURN_AGENT_CLAUDE_EFFORT=medium
```

---

## Worker CLI (no API needed)

The worker is also usable directly from the shell — useful for the eval harness or
local debugging. Same code path as the API.

```sh
source .venv/bin/activate

saturn-agent generate \
  --spec saturn-agent-eval/dataset/case_001_simple_workflow_node/spec.yaml \
  --workspace /tmp/saturn-test \
  --mock-script saturn-agent-eval/dataset/case_001_simple_workflow_node/mock_responses.json \
  --json-output

# Outputs:
#   - generated files in /tmp/saturn-test/
#   - /tmp/saturn-test/job_journal.json with the full event tape
```

Drop `--mock-script` to use real Claude/Gemini per `.env`.

---

## Conventions extractor

Produces the `conventions.json` artifact that the agent uses to match repo style.

```sh
source .venv/bin/activate

saturn-conventions extract \
  --repo /path/to/saturn-pipelines-reports \
  --out /tmp/conventions.json
```

Then plug it into a worker run with `--conventions /tmp/conventions.json`, or into
the API request body via `"conventions_path"`.

---

## Eval harness

Runs the worker against the dataset and grades its output.

```sh
source .venv/bin/activate

saturn-eval run case_001_simple_workflow_node \
  --output-root /tmp/saturn-eval-out \
  --report-json /tmp/saturn-eval-out/suite.json
```

Each case folder has its own `mock_responses.json`, so this works without API keys.

---

## Tests

With the venv activated:

```sh
# All four suites in one shot (~269 tests total)
pytest saturn-agent-worker/tests \
       saturn-agent-conventions/tests \
       saturn-agent-eval/tests \
       saturn-agent-api/tests
```

Or run them individually:

```sh
pytest saturn-agent-worker/tests        # ~225 tests
pytest saturn-agent-conventions/tests   # ~18 tests
pytest saturn-agent-eval/tests          # ~17 tests
pytest saturn-agent-api/tests           # ~9 tests
```

---

## What the agent does today

```
INIT → PLANNING → GENERATING → VALIDATING → TESTING → SUCCEEDED
                       ↑           ↓            ↓
                       └── REPAIRING ←──────────┘
                            (≤3 validation, ≤2 test attempts)
```

- Planner outputs a structured Plan (file list, descriptions, assumptions).
- Generator writes each file via `write_file`. One LLM call per file (plus tool round-trips).
- Validator runs `scalafmt`, `scalac`, and 5 text-based convention rules.
- Test stage runs `sbt` for pure-Scala specs (devbox/SSH stubbed).
- On VALIDATING/TESTING failure, the agent loops back through REPAIRING.
- Stops at SUCCEEDED. **PR creation is manual** — open the workspace,
  `git commit && git push`, raise the PR yourself.

Every LLM call and tool invocation is captured in a per-job journal:

- `<workspace>/job_journal.json` — full event tape
- `/api/jobs/{id}/events/replay` — same tape via JSON API
- `/api/jobs/{id}/events` — live SSE stream the frontend consumes

---

## Troubleshooting

**`saturn-api: command not found`** — activate the venv first: `source .venv/bin/activate`.

**Real LLM call fails with auth error** — make sure the right key is set in `.env`:
- Claude: `ANTHROPIC_API_KEY` (or `SATURN_AGENT_CLAUDE_API_KEY`)
- Gemini: `SATURN_AGENT_GEMINI_API_KEY`, plus `SATURN_API_LLM_PROVIDER=gemini`

**`.env` changes aren't picked up** — restart the API after editing `.env`. Settings
are loaded once at startup.

**Job stuck in `running`** — check the API stderr for tracebacks; the orchestrator
runs in a thread pool, so exceptions land there.

**Frontend can't reach the API** — the Vite dev server proxies `/api/*` to
`:8000`; make sure the API is up and listening on that port.

**"skills_root does not exist"** — usually fine without explicit config; the API
auto-locates the worker's `skills/` folder. Set `SATURN_API_SKILLS_ROOT` in
`.env` only if auto-detection picks the wrong path.
