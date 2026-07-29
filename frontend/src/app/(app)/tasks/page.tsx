import type { Metadata } from "next";
import { CalendarClock, CheckCircle2, CircleAlert, ListChecks, Plus } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { TaskBoard } from "@/components/tasks/task-board";
import { TaskDialog } from "@/components/tasks/task-dialog";
import { Button } from "@/components/ui/button";
import { getTaskStatusCounts, listTasks } from "@/lib/api/tasks";

export const metadata: Metadata = { title: "Tasks" };

export default async function TasksPage() {
  // The board renders a recent slice; the column badges and header stats use
  // the authoritative per-status counts, so the numbers stay honest even when
  // more tasks exist than are shown.
  const [page, counts] = await Promise.all([
    listTasks({ limit: 100 }),
    getTaskStatusCounts(),
  ]);

  const open =
    (counts.todo ?? 0) + (counts.in_progress ?? 0) + (counts.blocked ?? 0);
  const overdue = page.data.filter((t) => t.is_overdue).length;

  const stats: Stat[] = [
    { label: "Open tasks", value: String(open), hint: "across the team", icon: CircleAlert },
    { label: "In progress", value: String(counts.in_progress ?? 0), hint: "being worked now", icon: ListChecks },
    { label: "Blocked", value: String(counts.blocked ?? 0), hint: "waiting on something", icon: CalendarClock },
    { label: "Completed", value: String(counts.done ?? 0), hint: "done to date", icon: CheckCircle2 },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tasks"
        description="Follow-ups, paperwork and showing prep — grouped by status."
        actions={
          <TaskDialog
            mode="create"
            trigger={
              <Button>
                <Plus className="size-4" />
                New task
              </Button>
            }
          />
        }
      />

      <StatGrid stats={stats} />

      {overdue > 0 && (
        <p className="flex items-center gap-2 text-sm text-destructive">
          <CircleAlert className="size-4" />
          {overdue} of the tasks shown are overdue.
        </p>
      )}

      <TaskBoard tasks={page.data} counts={counts} />
    </div>
  );
}
