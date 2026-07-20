import type { Metadata } from "next";
import {
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  Link2,
  MoreHorizontal,
  Plus,
} from "lucide-react";

import { DataToolbar } from "@/components/shared/data-toolbar";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { tasks } from "@/lib/mock-data";
import { cn } from "@/lib/utils";
import type { Task, TaskStatus } from "@/types";

export const metadata: Metadata = { title: "Tasks" };

const stats: Stat[] = [
  { label: "Open tasks", value: "6", delta: -14.3, hint: "across the team", invertDelta: true, icon: CircleAlert },
  { label: "Due today", value: "2", hint: "1 is blocked", icon: CalendarClock },
  { label: "Completed this week", value: "17", delta: 21.4, hint: "vs. last week", icon: CheckCircle2 },
  { label: "Overdue", value: "0", hint: "nothing past due", icon: CheckCircle2 },
];

const columns: { status: TaskStatus; label: string; accent: string }[] = [
  { status: "todo", label: "To do", accent: "bg-muted-foreground/40" },
  { status: "in-progress", label: "In progress", accent: "bg-info" },
  { status: "blocked", label: "Blocked", accent: "bg-destructive" },
  { status: "done", label: "Done", accent: "bg-success" },
];

function TaskRow({ task }: { task: Task }) {
  const done = task.status === "done";

  return (
    <div className="flex items-start gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50">
      <Checkbox
        className="mt-0.5"
        defaultChecked={done}
        aria-label={`Mark "${task.title}" complete`}
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
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {task.description}
          </p>
        )}
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <StatusBadge status={task.priority} dot={false} />
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <CalendarClock className="size-3.5" />
            {task.dueDate}
          </span>
          {task.relatedTo && (
            <span className="flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              <Link2 className="size-3" />
              {task.relatedTo}
            </span>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <UserAvatar user={task.assignee} size="xs" />
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon-sm" aria-label="Task actions">
                <MoreHorizontal className="size-4" />
              </Button>
            }
          />
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem>Edit task</DropdownMenuItem>
            <DropdownMenuItem>Reassign</DropdownMenuItem>
            <DropdownMenuItem>Change due date</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive">Delete</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

export default function TasksPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Tasks"
        description="Follow-ups, paperwork and showing prep — grouped by status."
        actions={
          <Button>
            <Plus className="size-4" />
            New task
          </Button>
        }
      />

      <StatGrid stats={stats} />

      <Tabs defaultValue="board">
        <TabsList>
          <TabsTrigger value="board">Board</TabsTrigger>
          <TabsTrigger value="list">List</TabsTrigger>
          <TabsTrigger value="mine">My tasks</TabsTrigger>
        </TabsList>
      </Tabs>

      <DataToolbar placeholder="Search tasks…" />

      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-4">
        {columns.map((column) => {
          const columnTasks = tasks.filter((t) => t.status === column.status);

          return (
            <Card key={column.status} className="gap-0 py-0">
              <CardHeader className="flex-row items-center gap-2 border-b px-4 py-3">
                <span className={cn("size-2 rounded-full", column.accent)} />
                <CardTitle className="text-sm font-medium">
                  {column.label}
                </CardTitle>
                <span className="tabular ml-auto rounded-full bg-muted px-1.5 text-xs text-muted-foreground">
                  {columnTasks.length}
                </span>
              </CardHeader>

              <CardContent className="space-y-2.5 p-3">
                {columnTasks.length > 0 ? (
                  <>
                    {columnTasks.map((task) => (
                      <TaskRow key={task.id} task={task} />
                    ))}
                    <button className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed py-2.5 text-xs text-muted-foreground transition-colors hover:border-solid hover:bg-muted/50 hover:text-foreground">
                      <Plus className="size-3.5" />
                      Add task
                    </button>
                  </>
                ) : (
                  <EmptyState
                    compact
                    icon={CheckCircle2}
                    title="Nothing here"
                    description="Tasks in this state will appear in this column."
                    className="border-0"
                  />
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
