import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getJob, openEventStream, replayEvents } from "../api/jobs";
import { ApiError } from "../api/client";
import { JobEventResponse, TERMINAL_STAGES } from "../types/api";
import { StageBar } from "../components/StageBar";
import { EventStream } from "../components/EventStream";

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const id = jobId!;

  const { data: job, isLoading, error, refetch } = useQuery({
    queryKey: ["job", id],
    queryFn: () => getJob(id),
    retry: (count, err) =>
      err instanceof ApiError && err.status === 404 ? false : count < 3,
    refetchInterval: (q) => {
      const stage = q.state.data?.stage;
      return stage && TERMINAL_STAGES.has(stage) ? false : 1_000;
    },
  });

  const [events, setEvents] = useState<JobEventResponse[]>([]);

  useEffect(() => {
    let cancelled = false;
    replayEvents(id).then((initial) => {
      if (!cancelled) setEvents(initial);
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    const source = openEventStream(id);
    const handler = (e: MessageEvent) => {
      try {
        const parsed = JSON.parse(e.data) as JobEventResponse;
        setEvents((prev) =>
          prev.some((p) => p.created_at === parsed.created_at && p.event_type === parsed.event_type)
            ? prev
            : [...prev, parsed]
        );
        if (parsed.event_type.startsWith("job.")) {
          refetch();
        }
      } catch {
        /* ignore malformed payloads */
      }
    };
    // sse-starlette emits each event with `event:` set to the event_type, so
    // we listen on a wildcard via 'message' AND each known type. Listening
    // on 'message' alone won't catch typed events, so we add per-type
    // listeners for the events we care about.
    const knownTypes = [
      "job.created",
      "job.succeeded",
      "job.failed",
      "job.paused",
      "stage.entered",
      "stage.exited",
      "plan.parsed",
      "plan.parse_error",
      "file.generated",
      "generation.missing_file",
      "validation.passed",
      "validation.failed",
      "testing.passed",
      "testing.failed",
      "testing.skipped",
      "llm.called",
      "tool.invoked",
    ];
    knownTypes.forEach((t) => source.addEventListener(t, handler as EventListener));
    source.onerror = () => source.close();
    return () => {
      knownTypes.forEach((t) =>
        source.removeEventListener(t, handler as EventListener)
      );
      source.close();
    };
  }, [id, refetch]);

  if (isLoading) return <p className="p-6 text-sm text-slate-500">Loading…</p>;
  if (!job) return <p className="p-6 text-sm text-red-600">Not found</p>;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="space-y-1">
        <Link to="/" className="text-xs text-blue-600 hover:underline">
          ← All jobs
        </Link>
        <h1 className="font-mono text-lg text-slate-900">{job.job_id}</h1>
        <p className="text-sm text-slate-500">
          {job.target_repo} · {job.tokens_used} tokens · {job.user_id}
        </p>
      </header>

      <section>
        <StageBar current={job.stage} error={job.error} />
      </section>

      {job.open_questions.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-amber-700">
            Open questions ({job.open_questions.length})
          </h2>
          <ul className="rounded border border-amber-200 bg-amber-50 p-3 text-sm shadow-sm">
            {job.open_questions.map((q, i) => (
              <li
                key={i}
                className="border-b border-amber-100 py-1.5 last:border-b-0 text-amber-900"
              >
                {q}
              </li>
            ))}
          </ul>
        </section>
      )}

      {job.plan && (
        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Plan
          </h2>
          <ul className="rounded border border-slate-200 bg-white p-3 text-sm shadow-sm">
            {(job.plan.files ?? []).map((f) => (
              <li key={f.path} className="border-b border-slate-100 py-1.5 last:border-b-0">
                <span className="font-mono text-xs text-blue-700">{f.path}</span>
                <span className="ml-2 text-xs text-slate-500">{f.kind}</span>
                <p className="text-xs text-slate-700">{f.description}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Events ({events.length})
        </h2>
        <EventStream events={events} />
      </section>
    </div>
  );
}
