import { Badge } from "@/components/ui/badge";
import type { WorkflowRunStatus } from "@/lib/api/types";

const LABELS: Record<WorkflowRunStatus, string> = {
  pending: "Queued",
  running: "Running",
  waiting: "Waiting",
  succeeded: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

/**
 * `waiting` gets its own look rather than folding into "running": a run parked
 * on a three-day delay is healthy, and showing it as in-progress makes an
 * operator scanning the log think something is stuck.
 */
export function RunStatusBadge({ status }: { status: WorkflowRunStatus }) {
  return (
    <Badge
      variant={
        status === "failed"
          ? "destructive"
          : status === "succeeded"
            ? "default"
            : status === "waiting"
              ? "outline"
              : "secondary"
      }
    >
      {LABELS[status] ?? status}
    </Badge>
  );
}
