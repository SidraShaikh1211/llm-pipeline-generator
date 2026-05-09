import { Stage, VISIBLE_STAGES, TERMINAL_STAGES } from "../types/api";

interface Props {
  current: Stage;
  error: string | null;
}

export function StageBar({ current, error }: Props) {
  const failed = current === "failed";
  const paused = current === "paused_for_user";
  const currentIdx = VISIBLE_STAGES.indexOf(current);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {VISIBLE_STAGES.map((s, i) => {
          const reached =
            currentIdx >= i ||
            (TERMINAL_STAGES.has(current) && current !== "failed" && i < VISIBLE_STAGES.length);
          const active = current === s;
          return (
            <div key={s} className="flex flex-1 items-center gap-2">
              <div
                className={[
                  "flex h-8 w-8 items-center justify-center rounded-full border text-xs font-medium",
                  active
                    ? "border-blue-500 bg-blue-500 text-white"
                    : reached
                    ? "border-green-500 bg-green-500 text-white"
                    : "border-slate-300 bg-white text-slate-500",
                ].join(" ")}
                title={s}
              >
                {i + 1}
              </div>
              <span className="text-xs text-slate-700 capitalize">{s.replace("_", " ")}</span>
              {i < VISIBLE_STAGES.length - 1 && (
                <div
                  className={[
                    "mx-2 h-px flex-1",
                    reached ? "bg-green-500" : "bg-slate-300",
                  ].join(" ")}
                />
              )}
            </div>
          );
        })}
      </div>
      {failed && (
        <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          <strong>FAILED:</strong> {error ?? "no error captured"}
        </div>
      )}
      {paused && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <strong>Paused for user:</strong> {error ?? "answer the open questions to continue"}
        </div>
      )}
    </div>
  );
}
