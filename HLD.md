# Saturn Agent — High-Level Design

## 1. Goals

Build a hosted internal tool that takes a saturn pipeline spec, generates Scala code + DDL + tests, runs the tests, and opens a PR against the appropriate repo's `develop` branch — minimal manual work for the user beyond reviewing and approving.

Targets:
- 5–30 internal users
- Hosted internally (own VM / k8s cluster, internal network only)
- Internal Gemini API as the LLM
- Output lands as a real PR, not just files
- Auditable end-to-end (every action attributed to a user)

## 2. Non-goals (v1)

- Multi-LLM provider abstraction (Gemini is the only model)
- Auto-deploy to UAT (separate phase)
- Editing existing pipelines beyond simple node additions (focused-edit story is phase 2)
- Multi-repo atomic changes orchestrated by the agent (the agent handles one logical change at a time; cross-repo changes are sequential PRs)
- Spark performance analysis agent (Agent #2 from the original brainstorm — separate project)

## 3. The "agent" framing

A single agent with multiple skills, wrapped by a deterministic orchestrator. **Not** a multi-agent dance.

```
┌─────────────────────────────────────────────────────────────┐
│                     Orchestrator (Python)                   │
│  Deterministic state machine — knows when to call what.    │
│                                                             │
│   plan ──► generate ──► validate ──► test ──► commit ──►PR  │
│              ▲           │                                  │
│              └───────────┘  (retry on validation failure)   │
└─────────────────────────────────────────────────────────────┘
                          │
                          │  invokes per-stage
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Single LLM Agent                         │
│  Gemini + tools + skill-as-system-prompt                   │
│  Different "modes" per orchestrator stage:                 │
│    - planning mode (which skills, decomposition)           │
│    - generation mode (one of three skills active)          │
│    - repair mode (fix validator-flagged issues)            │
└─────────────────────────────────────────────────────────────┘
                          │
                          │  uses tools
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Tool surface (deterministic, no LLM)                      │
│  - repo nav: read_file, list_dir, grep, find_sibling       │
│  - codebase: read_skill, read_reference, read_conventions  │
│  - emit:     write_file, scaffold_test_fixture             │
│  - validate: scalac, scalafmt, convention_check            │
│  - test:     run_local_tests, run_devbox_tests             │
│  - vcs:      git_branch, git_commit, git_push              │
│  - pr:       bitbucket_open_pr, bitbucket_get_pr_status    │
└─────────────────────────────────────────────────────────────┘
```

The orchestrator is **dumb on purpose** — it's a finite state machine with explicit transitions, not an LLM-driven loop. The LLM is only invoked at well-defined stages. This makes the system debuggable, replayable, and bounded in cost.

## 4. System architecture

```
                       ┌───────────────────┐
                       │   React Frontend  │
                       │   (Next.js or     │
                       │    Vite + RTK)    │
                       └─────────┬─────────┘
                                 │ HTTPS, SSO cookie
                                 ▼
                       ┌───────────────────┐
                       │   FastAPI Server  │
                       │   (auth, jobs,    │
                       │    SSE for logs)  │
                       └────┬───────┬──────┘
                            │       │
              ┌─────────────┘       └────────────────┐
              │                                      │
              │ enqueue                              │ read state
              ▼                                      ▼
       ┌──────────────┐                        ┌──────────────┐
       │ Job queue    │                        │  PostgreSQL  │
       │ (Redis +     │◄──────── update ───────│  (jobs, runs,│
       │  Celery)     │                        │   audit log) │
       └──────┬───────┘                        └──────────────┘
              │ dequeue
              ▼
       ┌─────────────────────────────────────────────┐
       │           Worker pool (k8s pods)            │
       │                                             │
       │   ┌─────────────────────────────────────┐   │
       │   │   Orchestrator                      │   │
       │   │     ↓                               │   │
       │   │   Agent (Gemini)                    │   │
       │   │     ↓                               │   │
       │   │   Tool execution                    │   │
       │   └─────────────────────────────────────┘   │
       │                                             │
       │   Workspace: /workspace/<job_id>/           │
       │     ├── repos/saturn-pipelines-reports/    │
       │     ├── repos/saturn-hadoop-ddl/           │
       │     └── outputs/                           │
       └────┬────────────────────────────┬───────────┘
            │                            │
            │ LLM calls                  │ test execution (heavy)
            ▼                            ▼
     ┌────────────────┐           ┌────────────────┐
     │ Internal       │           │  User devbox   │
     │ Gemini API     │           │  (over SSH,    │
     │                │           │   per user)    │
     └────────────────┘           └────────────────┘
            │                            │
            │ also writes to             │
            ▼                            ▼
     ┌────────────────┐           ┌────────────────┐
     │  MinIO         │           │  Bitbucket     │
     │  (artifacts,   │           │  (push branch, │
     │   transcripts) │           │   open PR)     │
     └────────────────┘           └────────────────┘
```

### Why these pieces

- **Postgres for state, Redis for queue.** Postgres is the source of truth for job state, audit log, eval results. Redis is the message bus for job dispatch + transient SSE pub/sub. Don't conflate them.
- **Celery, not RQ.** Celery has better support for chains/groups, retry policies with backoff, and result backends. Saturn jobs have natural sub-tasks (plan → generate → validate → test → PR) that benefit from chaining.
- **Workers in k8s, tests on devbox.** Critical decision: the worker pod does all the lightweight work (LLM calls, file generation, scalac compile, simple unit tests). Anything that needs Spark/Hive is shelled out over SSH to the requesting user's devbox. This avoids putting Spark in the worker container and avoids licensing/access issues with shared cluster credentials.
- **MinIO for artifacts.** S3-compatible on-prem object store. Stores generated files, agent transcripts, test outputs, eval traces.
- **No Vault yet.** Secrets in Kubernetes Secrets for v1. Add Vault when secret rotation becomes a real concern (probably v2).

## 5. The five repos (renamed for clarity)

```
1. saturn-agent-frontend       — React app
2. saturn-agent-api            — FastAPI server
3. saturn-agent-worker         — Orchestrator + agent + tools
4. saturn-agent-conventions    — Convention extractor (offline cron job)
5. saturn-agent-eval           — Eval harness, dataset, scoring
```

These are the agent project's own repos. The existing saturn repos (`saturn-pipelines-reports`, `saturn-hadoop-ddl`, etc.) are the **target** — read by the worker, not part of the agent codebase.

Why split them: different deploy cadences, different review groups (frontend vs backend vs ML eng for eval), different test surfaces. Conventions extractor in particular runs offline and can deploy independently of everything else.

## 6. End-to-end happy path

```
1. User opens web UI, selects:
   - Pipeline domain name (e.g., "repo_funding")
   - Target repo
   - Inputs/outputs (table names + schemas, paste from spec doc)
   - Transformations (textarea, mechanism-level)
   - JIRA ticket
   - Devbox hostname (their own GCP VM)

2. UI POSTs job to /api/jobs.
   API validates, persists to Postgres, enqueues to Celery.
   Returns job_id; UI subscribes to /api/jobs/{id}/events (SSE).

3. Worker dequeues. Initializes workspace at /workspace/<job_id>/.
   Clones target repos (or pulls fresh) via service-account creds.
   Loads conventions cache for the target pipeline.

4. Orchestrator: PLANNING stage.
   Agent reads spec + conventions, decides:
   - Which skills are needed (saturn-pipeline-builder always; ddl-builder
     if output table is new; pipeline-tester for tests)
   - Decomposition: how many nodes, single-node vs multi-node DAG
   - List of files to be created, order of creation
   Outputs structured plan (JSON), persisted.

5. Orchestrator: GENERATION stage. Loops over the plan.
   For each file, agent generates code (skill loaded as system prompt,
   relevant references attached). File written to workspace.

6. Orchestrator: VALIDATION stage.
   - scalafmt
   - scalac compile
   - convention_check (deterministic rules)
   - On failure → REPAIR stage (agent gets compile errors, fixes; bounded
     to 3 attempts before giving up and surfacing to user)

7. Orchestrator: TEST stage.
   - Local: any pure-Scala unit test (no Spark) runs in worker pod.
   - Devbox: any test needing Spark gets pushed to user's devbox over
     SSH, executed with their credentials, output streamed back.
   - Failures → REPAIR stage (max 2 attempts; test failures are often
     spec ambiguity, not code bugs, so we surface earlier than compile).

8. Orchestrator: PR stage.
   - git checkout -b feature/<JIRA>-<slug>
   - git add + commit (agent generates message)
   - git push
   - bitbucket API: open PR to develop with template body
   - Persist PR URL to job record.

9. UI receives final SSE event with PR URL. Job status = SUCCEEDED.
   User reviews PR.

10. User can re-trigger from the UI ("regenerate with these changes")
    which creates a follow-up job linked to the original.
```

## 7. Failure modes and how the orchestrator handles them

| Stage | Failure | Action |
|---|---|---|
| Planning | LLM returns malformed JSON | Retry once with stricter prompt; then fail job with helpful error |
| Planning | Spec is incomplete (missing volume for new DDL) | Surface to user, pause job, wait for input via UI |
| Generation | LLM hits content/safety filter | Retry with rephrased context; rare but happens |
| Generation | Tool error (file not found) | Retry; if persistent, fail with diagnostic |
| Validation | scalac compile error | Send error back to agent, REPAIR mode (3 attempts) |
| Validation | Convention check fail (e.g., missing `.finish()`) | REPAIR mode (3 attempts; cheaper than compile errors) |
| Test | Local unit test fail | REPAIR mode (2 attempts) |
| Test | Devbox SSH down | Pause job, notify user, resume on retry |
| Test | Spark test fail | REPAIR mode (2 attempts); often spec ambiguity, surface details |
| PR | Branch already exists | Append timestamp to branch name; warn user |
| PR | Bitbucket API down | Retry with backoff (Celery handles); fail after 3 retries with manual-push instructions |

Every failure persists the full state. User can resume from the UI, agent picks up where it left off.

## 8. Workspace and concurrency model

**One workspace per job, ephemeral.** Path: `/workspace/<job_id>/`.

- Repos cloned fresh per job (or git-cleaned-and-pulled if reusing).
- Workspace is destroyed after job completion (or after 7 days for debug).
- Workers run jobs sequentially (one job per worker pod). Horizontal scale = more pods.
- No shared mutable state between jobs.

Why fresh clones: avoids two concurrent jobs racing on the same checkout. Disk cost is negligible (saturn repos are < 500 MB). The performance cost of cloning is dwarfed by LLM call time.

**Devbox concurrency**: a user can have multiple jobs in flight, all targeting the same devbox. SSH commands are queued per-devbox by the worker (a Redis lock keyed on `devbox:<host>`) so two jobs don't fight over `sbt test` simultaneously.

## 9. Identity, auth, secrets

- **User auth**: SSO (LDAP / SAML — match firm's standard). FastAPI middleware validates session cookie / JWT. Every API call attributed to a user.
- **Service account**: One bot account in Bitbucket with PR creation rights. Token in k8s Secret. Used by worker for git operations.
- **Devbox auth**: User's own SSH key. Either user uploads their key once (encrypted at rest, scoped to their account) or the system uses short-lived signed certs via the firm's existing SSH CA if available. The latter is much better — investigate.
- **LLM auth**: Internal Gemini API key in k8s Secret. One key for all workers; per-user rate limiting enforced at API gateway, not via separate keys.
- **Audit**: Every action (job created, PR opened, devbox SSH command run) logged to Postgres `audit_log` with `(user, action, resource, timestamp, outcome)`. Retain 1 year minimum (firm's typical compliance window).

## 10. Observability

Three layers, all of them required:

- **Logs**: Structured JSON logs (one log line per event), shipped to your existing log aggregator (Splunk / ELK / whatever the firm uses). Every line includes `job_id`, `user`, `stage`.
- **Metrics**: Prometheus + Grafana. Track: jobs per status (queued/running/succeeded/failed), latency per stage (planning/generation/validation/test/PR), LLM token usage per job, repair-loop iteration count, devbox SSH command duration.
- **Traces**: OpenTelemetry. One trace per job, span per orchestrator stage, sub-spans per LLM call and per tool call. Massive debugging win — you can see exactly which LLM call timed out or which tool returned the bad result.

Plus a per-job replay artifact in MinIO: full prompt + response transcript for every LLM call, every tool input/output. When a job fails in surprising ways, you replay the transcript locally to debug. This is non-negotiable for an LLM-driven system.

## 11. Convention extractor

Runs offline (cron, daily) over each target repo:

```
saturn-agent-conventions/
  ├── extractor.py            # tree-sitter-based parser
  ├── schemas/
  │   └── conventions.schema.json
  └── output/
      ├── saturn-pipelines-reports.conventions.json
      └── saturn-hadoop-ddl.conventions.json
```

Extracts per-pipeline:
- Style (A/B/C)
- Constructor param order conventions
- Naming conventions (camelCase vs snake_case for Scala identifiers)
- `globalArg` / `nodeArg` pairs in use
- `Common` case class shape (if multi-node)
- Imports actually used in this repo (vs the cheat-sheet)
- DDL filename pattern, table schema conventions

Output is published to MinIO. Worker pulls the latest at job start, attaches the relevant slice to the agent's context.

This is the single biggest lever for code quality with weaker LLMs. Re-extracting the conventions every job via LLM crawling is wasteful and error-prone; doing it once mechanically with a real parser is fast, cheap, deterministic.

**Build this early.** It's the load-bearing artifact for everything else.

## 12. Eval harness

Without this you cannot measure progress.

```
saturn-agent-eval/
  ├── dataset/
  │   ├── case_001_simple_workflow_node/
  │   │   ├── spec.yaml             # input
  │   │   ├── expected_files/       # ground truth output (from real merged PR)
  │   │   └── grading.yaml          # what to check
  │   ├── case_002_multi_node_dag_with_ddl/
  │   └── ...
  ├── runner.py                     # invokes the worker against the dataset
  └── grader.py                     # mechanical scoring
```

Dataset: pick 8–12 real saturn PRs from git history. For each, the spec is the JIRA description + table schemas; the expected output is the actual merged code.

Grading axes (mechanical, no human judgment in v1):
- **Compile** — does the generated code compile against saturn's classpath?
- **Convention** — does it pass the static convention checks?
- **Provider parity** — does the generated Provider chain have the same nodes and same edges as the ground truth (set equality, not identity)?
- **DDL parity** — same columns, same partitioning?
- **Test pass rate** — do the generated tests actually pass?

Run the eval on every commit to `saturn-agent-worker`. Block merges that regress on the eval suite.

Build this in week 1. **Without it you have no signal.**

## 13. Risks and open questions

| Risk | Mitigation |
|---|---|
| Gemini quality on Scala/Spark | Eval harness early; fall back on prompt engineering / examples in references/ if results are bad. Reserve option to use a stronger model via API exception if available. |
| Devbox SSH auth complexity | Investigate firm's existing SSH CA before building user-uploads-key path. |
| Bitbucket API rate limits | Cache PR templates; one PR per job; should be fine. |
| Concurrent jobs on same target repo | Fresh clone per job; service account commits are linearizable on Bitbucket side. |
| Spec ambiguity surfaces as test failures | Repair loop is bounded (2 attempts for tests); fail fast and ask user. Avoid the "agent spirals trying to satisfy a contradictory spec" trap. |
| Convention drift breaking the cache | Daily extractor cron + manual re-extract API endpoint when team merges convention-changing PRs. Eval suite catches gross drift. |
| Cost runaway on long agent loops | Hard token-budget per job (e.g., 500k tokens). Orchestrator aborts if exceeded. Surface to user with partial work. |

## 14. Build order (independent of timeline)

In dependency order, not time order. Each layer assumes the previous works.

1. **Eval harness + 5 dataset cases.** Without this you cannot measure quality of anything else.
2. **Convention extractor MVP.** One pipeline parsed end-to-end into JSON. Becomes the load-bearing input for the agent.
3. **Worker core: orchestrator + tool surface + Gemini client.** No queue, no API, no UI — just `python -m saturn_agent.cli generate --spec spec.yaml --repo /path` running locally on a dev machine. Iterate on quality against the eval suite.
4. **Tools: validation (scalac, scalafmt, convention_check).** Deterministic; biggest single quality lever after the conventions cache.
5. **Tools: test execution (local-only first, devbox second).**
6. **Tools: VCS + PR (git ops + Bitbucket API).**
7. **API + Postgres + Celery + Redis.** Wraps the now-working worker for hosting.
8. **Frontend.** Build last. Mocks the API while it stabilizes.
9. **Observability.** Add as you go; do not skip; do not defer.
10. **SSO + audit log.** Required before any non-developer touches it.

This order means you have a working agent on the CLI by the end of step 6 — fully usable by you and one or two collaborators while the hosting layer is being built. That's the right shape: validate the agent works before investing in the surrounding infrastructure.
