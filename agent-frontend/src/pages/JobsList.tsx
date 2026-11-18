import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createJob, listJobs } from "../api/jobs";

export function JobsList() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    refetchInterval: 2_000,
  });

  const [showForm, setShowForm] = useState(false);
  const [pipelineName, setPipelineName] = useState("foo_reports");
  const [targetRepo, setTargetRepo] = useState("pipelines-reports");
  const [transformations, setTransformations] = useState("");
  const [mockScriptPath, setMockScriptPath] = useState("");
  const [targetRepoSource, setTargetRepoSource] = useState("");
  const [conventionsPath, setConventionsPath] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createJob({
        spec: {
          pipeline_name: pipelineName,
          target_repo: targetRepo,
          transformations,
        },
        target_repo: targetRepo,
        mock_script_path: mockScriptPath || undefined,
        target_repo_source: targetRepoSource || undefined,
        conventions_path: conventionsPath || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setShowForm(false);
    },
  });

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">LLM Pipeline Generator</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          {showForm ? "Cancel" : "New job"}
        </button>
      </header>

      {showForm && <NewJobForm
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          create.mutate();
        }}
        pipelineName={pipelineName}
        setPipelineName={setPipelineName}
        targetRepo={targetRepo}
        setTargetRepo={setTargetRepo}
        transformations={transformations}
        setTransformations={setTransformations}
        mockScriptPath={mockScriptPath}
        setMockScriptPath={setMockScriptPath}
        targetRepoSource={targetRepoSource}
        setTargetRepoSource={setTargetRepoSource}
        conventionsPath={conventionsPath}
        setConventionsPath={setConventionsPath}
        error={create.error as Error | null}
        submitting={create.isPending}
      />}

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Recent jobs
        </h2>
        {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {error && <p className="text-sm text-red-600">{(error as Error).message}</p>}
        {data && data.jobs.length === 0 && (
          <p className="text-sm text-slate-500">No jobs yet — click "New job".</p>
        )}
        {data && data.jobs.length > 0 && (
          <table className="w-full rounded border border-slate-200 bg-white text-sm shadow-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">Job</th>
                <th className="px-3 py-2">Repo</th>
                <th className="px-3 py-2">Stage</th>
                <th className="px-3 py-2">Tokens</th>
                <th className="px-3 py-2">Created</th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((j) => (
                <tr key={j.job_id} className="border-t border-slate-100">
                  <td className="px-3 py-2">
                    <Link
                      to={`/jobs/${j.job_id}`}
                      className="font-mono text-xs text-blue-600 hover:underline"
                    >
                      {j.job_id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="px-3 py-2">{j.target_repo}</td>
                  <td className="px-3 py-2">
                    <StagePill stage={j.stage} />
                  </td>
                  <td className="px-3 py-2 tabular-nums">{j.tokens_used}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">
                    {new Date(j.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function StagePill({ stage }: { stage: string }) {
  const color =
    stage === "succeeded"
      ? "bg-green-100 text-green-800"
      : stage === "failed"
      ? "bg-red-100 text-red-800"
      : stage === "paused_for_user"
      ? "bg-amber-100 text-amber-800"
      : "bg-blue-100 text-blue-800";
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${color}`}>
      {stage}
    </span>
  );
}

interface FormProps {
  onSubmit: (e: FormEvent) => void;
  pipelineName: string;
  setPipelineName: (v: string) => void;
  targetRepo: string;
  setTargetRepo: (v: string) => void;
  transformations: string;
  setTransformations: (v: string) => void;
  mockScriptPath: string;
  setMockScriptPath: (v: string) => void;
  targetRepoSource: string;
  setTargetRepoSource: (v: string) => void;
  conventionsPath: string;
  setConventionsPath: (v: string) => void;
  error: Error | null;
  submitting: boolean;
}

function NewJobForm(props: FormProps) {
  return (
    <form
      onSubmit={props.onSubmit}
      className="rounded border border-slate-200 bg-white p-4 shadow-sm"
    >
      <div className="grid grid-cols-2 gap-4">
        <Field label="Pipeline name">
          <input
            value={props.pipelineName}
            onChange={(e) => props.setPipelineName(e.target.value)}
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
            required
          />
        </Field>
        <Field label="Target repo">
          <input
            value={props.targetRepo}
            onChange={(e) => props.setTargetRepo(e.target.value)}
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
            required
          />
        </Field>
        <Field label="Transformations" className="col-span-2">
          <textarea
            value={props.transformations}
            onChange={(e) => props.setTransformations(e.target.value)}
            rows={3}
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm font-mono"
          />
        </Field>
        <Field label="Target repo source (git URL or local path, optional)" className="col-span-2">
          <input
            value={props.targetRepoSource}
            onChange={(e) => props.setTargetRepoSource(e.target.value)}
            placeholder="https://github.com/org/repo  OR  /Users/me/pipelines-reports"
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm font-mono"
          />
        </Field>
        <Field label="Conventions JSON path (optional)" className="col-span-2">
          <input
            value={props.conventionsPath}
            onChange={(e) => props.setConventionsPath(e.target.value)}
            placeholder="/tmp/conventions.json (run `agent-conventions extract` to produce)"
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm font-mono"
          />
        </Field>
        <Field label="Mock script path (server-local)" className="col-span-2">
          <input
            value={props.mockScriptPath}
            onChange={(e) => props.setMockScriptPath(e.target.value)}
            placeholder="/path/to/mock_responses.json (leave empty for real Claude/Gemini)"
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm font-mono"
          />
        </Field>
      </div>
      {props.error && (
        <p className="mt-3 text-sm text-red-600">{props.error.message}</p>
      )}
      <div className="mt-3 flex justify-end">
        <button
          type="submit"
          disabled={props.submitting}
          className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {props.submitting ? "Creating…" : "Create job"}
        </button>
      </div>
    </form>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`block text-sm ${className ?? ""}`}>
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </span>
      {children}
    </label>
  );
}
