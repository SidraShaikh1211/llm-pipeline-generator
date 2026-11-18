import { JobEventResponse } from "../types/api";

interface Props {
  events: JobEventResponse[];
}

export function EventStream({ events }: Props) {
  if (events.length === 0) {
    return (
      <div className="rounded border border-dashed border-slate-300 p-4 text-sm text-slate-500">
        No events yet.
      </div>
    );
  }
  return (
    <ol className="space-y-1 font-mono text-xs">
      {events.map((e, i) => {
        const time = new Date(e.created_at).toISOString().slice(11, 23);
        return (
          <li key={i} className="flex gap-2 rounded bg-white px-2 py-1 shadow-sm">
            <span className="text-slate-400">{time}</span>
            <span className="w-24 shrink-0 text-purple-700">{e.stage}</span>
            <span className="w-40 shrink-0 text-blue-700">{e.event_type}</span>
            <span className="text-slate-700">
              {Object.keys(e.payload).length > 0
                ? JSON.stringify(e.payload)
                : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
