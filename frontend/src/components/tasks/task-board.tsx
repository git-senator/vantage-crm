"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  CalendarClock,
  CheckCircle2,
  Link2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/shared/empty-state";
import { OwnerAvatar } from "@/components/shared/owner-avatar";
import { StatusBadge } from "@/components/shared/status-badge";
import { TaskDialog } from "@/components/tasks/task-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ClientApiError } from "@/lib/api/client";
import { completeTask, deleteTask, reopenTask } from "@/lib/api/tasks-client";
import { cn } from "@/lib/utils";
import type { Task, TaskStatus } from "@/lib/api/types";

const COLUMNS: { status: TaskStatus; label: string; accent: string }[] = [
  { status: "todo", label: "To do", accent: "bg-muted-foreground/40" },
  { status: "in_progress", label: "In progress", accent: "bg-info" },
  { status: "blocked", label: "Blocked", accent: "bg-destructive" },
  { status: "done", label: "Done", accent: "bg-success" },
];

const ENTITY_PATH: Record<string, string> = {
  lead: "/leads",
  client: "/clients",
  property: "/properties",
  deal: "/deals",
};

function DeleteTaskDialog({ task }: { task: Task }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setPending(true);
    setError(null);
    try {
      await deleteTask(task.id);
      setOpen(false);
      toast.success("Task deleted");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Unable to delete. Please try again.",
      );
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DropdownMenuItem
        variant="destructive"
        onSelect={(e) => {
          e.preventDefault();
          setOpen(true);
        }}
      >
        <Trash2 className="size-4" />
        Delete
      </DropdownMenuItem>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete this task?</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          “{task.title}” will be removed. This cannot be undone from here.
        </p>
        {error && (
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <DialogFooter>
          <DialogClose
            render={<Button variant="ghost" disabled={pending}>Cancel</Button>}
          />
          <Button variant="destructive" onClick={handleDelete} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            Delete task
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TaskCard({ task }: { task: Task }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const done = task.status === "done";

  async function toggleDone() {
    setPending(true);
    try {
      if (done) {
        await reopenTask(task.id);
      } else {
        await completeTask(task.id);
      }
      router.refresh();
    } catch (caught) {
      toast.error(
        caught instanceof ClientApiError ? caught.message : "Could not update task",
      );
    } finally {
      setPending(false);
    }
  }

  const due = task.due_at ? new Date(task.due_at) : null;

  return (
    <div className="flex items-start gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50">
      <Checkbox
        className="mt-0.5"
        checked={done}
        disabled={pending}
        onCheckedChange={toggleDone}
        aria-label={done ? `Reopen "${task.title}"` : `Complete "${task.title}"`}
      />
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "text-sm leading-snug font-medium",
            done && "text-muted-foreground line-through",
          )}
        >
          {task.title}
        </p>
        {task.description && (
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
            {task.description}
          </p>
        )}
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <StatusBadge status={task.priority} dot={false} />
          {due && (
            <span
              className={cn(
                "flex items-center gap-1 text-xs",
                task.is_overdue ? "text-destructive" : "text-muted-foreground",
              )}
              suppressHydrationWarning
            >
              <CalendarClock className="size-3.5" />
              {due.toLocaleDateString()}
            </span>
          )}
          {task.entity_type && task.entity_id && ENTITY_PATH[task.entity_type] && (
            <Link
              href={`${ENTITY_PATH[task.entity_type]}/${task.entity_id}`}
              className="flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground capitalize transition-colors hover:text-foreground"
            >
              <Link2 className="size-3" />
              {task.entity_type}
            </Link>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {task.assignee && <OwnerAvatar owner={task.assignee} size="xs" />}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon-sm" aria-label="Task actions">
                <MoreHorizontal className="size-4" />
              </Button>
            }
          />
          <DropdownMenuContent align="end" className="w-40">
            <TaskDialog
              mode="edit"
              task={task}
              trigger={
                <DropdownMenuItem
                  onSelect={(e) => e.preventDefault()}
                >
                  <Pencil className="size-4" />
                  Edit task
                </DropdownMenuItem>
              }
            />
            <DropdownMenuSeparator />
            <DeleteTaskDialog task={task} />
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

export function TaskBoard({
  tasks,
  counts = {},
}: {
  tasks: Task[];
  /** Authoritative per-status totals; the board renders only a recent subset. */
  counts?: Record<string, number>;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-4">
      {COLUMNS.map((column) => {
        const columnTasks = tasks.filter((t) => t.status === column.status);
        const total = counts[column.status] ?? columnTasks.length;

        return (
          <Card key={column.status} className="gap-0 py-0">
            <CardHeader className="flex-row items-center gap-2 border-b px-4 py-3">
              <span className={cn("size-2 rounded-full", column.accent)} />
              <CardTitle className="text-sm font-medium">{column.label}</CardTitle>
              <span className="tabular ml-auto rounded-full bg-muted px-1.5 text-xs text-muted-foreground">
                {total}
              </span>
            </CardHeader>

            <CardContent className="space-y-2.5 p-3">
              {columnTasks.length > 0 ? (
                columnTasks.map((task) => <TaskCard key={task.id} task={task} />)
              ) : (
                <EmptyState
                  compact
                  icon={CheckCircle2}
                  title="Nothing here"
                  description="Tasks in this state will appear in this column."
                  className="border-0"
                />
              )}

              {column.status !== "done" && (
                <TaskDialog
                  mode="create"
                  defaultStatus={column.status}
                  trigger={
                    <button className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed py-2.5 text-xs text-muted-foreground transition-colors hover:border-solid hover:bg-muted/50 hover:text-foreground">
                      <Plus className="size-3.5" />
                      Add task
                    </button>
                  }
                />
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
