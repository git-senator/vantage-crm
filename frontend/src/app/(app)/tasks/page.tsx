import type { Metadata } from "next";
import { CalendarClock, CheckCircle2, CircleAlert, ListChecks, Plus } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { TaskBoard } from "@/components/tasks/task-board";
import { TaskDialog } from "@/components/tasks/task-dialog";
import { Button } from "@/components/ui/button";
import { getTranslations } from "@/i18n/server";
import { getTaskStatusCounts, listTasks } from "@/lib/api/tasks";

export const metadata: Metadata = { title: "Tasks" };

export default async function TasksPage() {
  // The board renders a recent slice; the column badges and header stats use
  // the authoritative per-status counts, so the numbers stay honest even when
  // more tasks exist than are shown.
  const [page, counts, t] = await Promise.all([
    listTasks({ limit: 100 }),
    getTaskStatusCounts(),
    getTranslations(),
  ]);

  const open =
    (counts.todo ?? 0) + (counts.in_progress ?? 0) + (counts.blocked ?? 0);
  const overdue = page.data.filter((task) => task.is_overdue).length;

  const stats: Stat[] = [
    { label: t("body.tasksOpenLabel"), value: String(open), hint: t("body.tasksHintTeam"), icon: CircleAlert },
    { label: t("body.tasksInProgressLabel"), value: String(counts.in_progress ?? 0), hint: t("body.tasksHintProgress"), icon: ListChecks },
    { label: t("body.tasksBlockedLabel"), value: String(counts.blocked ?? 0), hint: t("body.tasksHintBlocked"), icon: CalendarClock },
    { label: t("body.tasksCompletedLabel"), value: String(counts.done ?? 0), hint: t("body.tasksHintDone"), icon: CheckCircle2 },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.tasksTitle")}
        description={t("body.tasksDesc")}
        actions={
          <TaskDialog
            mode="create"
            trigger={
              <Button>
                <Plus className="size-4" />
                {t("body.newTask")}
              </Button>
            }
          />
        }
      />

      <StatGrid stats={stats} />

      {overdue > 0 && (
        <p className="flex items-center gap-2 text-sm text-destructive">
          <CircleAlert className="size-4" />
          {t("body.tasksOverdueBanner", { n: overdue })}
        </p>
      )}

      <TaskBoard tasks={page.data} counts={counts} />
    </div>
  );
}
