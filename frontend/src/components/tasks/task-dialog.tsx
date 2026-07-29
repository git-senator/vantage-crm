"use client";

import { useRouter } from "next/navigation";
import {
  useState,
  type FormEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { Loader2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ClientApiError } from "@/lib/api/client";
import { createTask, updateTask } from "@/lib/api/tasks-client";
import type { Task, TaskPriority, TaskStatus } from "@/lib/api/types";

type OpenStatus = Exclude<TaskStatus, "done">;

const PRIORITIES: TaskPriority[] = ["low", "medium", "high", "urgent"];
const OPEN_STATES: { value: OpenStatus; label: string }[] = [
  { value: "todo", label: "To do" },
  { value: "in_progress", label: "In progress" },
  { value: "blocked", label: "Blocked" },
];

/** ISO → the `datetime-local` input's `YYYY-MM-DDTHH:mm`, in the viewer's zone. */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * Create or edit a task.
 *
 * Create defaults `status` to whichever column launched it; the API always
 * starts a task as `todo`, so a non-todo choice is applied with a follow-up
 * update. Moving a task to `done` is not offered here — that is the checkbox on
 * the row, which runs the completion flow.
 */
export function TaskDialog({
  mode,
  task,
  defaultStatus = "todo",
  trigger,
}: {
  mode: "create" | "edit";
  task?: Task;
  defaultStatus?: OpenStatus;
  trigger: ReactNode;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [priority, setPriority] = useState<TaskPriority>(task?.priority ?? "medium");
  const [status, setStatus] = useState<OpenStatus>(
    (task?.status as OpenStatus) ?? defaultStatus,
  );
  const [dueAt, setDueAt] = useState(toLocalInput(task?.due_at ?? null));

  function reset() {
    setError(null);
    if (mode === "create") {
      setTitle("");
      setDescription("");
      setPriority("medium");
      setStatus(defaultStatus);
      setDueAt("");
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) {
      setError("A title is required.");
      return;
    }
    setPending(true);
    setError(null);
    const due = dueAt ? new Date(dueAt).toISOString() : null;

    try {
      if (mode === "create") {
        const created = await createTask({
          title: title.trim(),
          description: description.trim() || null,
          priority,
          due_at: due,
        });
        // The API starts every task as `todo`; honour a different column.
        if (status !== "todo") {
          await updateTask(created.id, { status });
        }
        toast.success("Task created");
      } else if (task) {
        await updateTask(task.id, {
          title: title.trim(),
          description: description.trim() || null,
          priority,
          status,
          due_at: due,
        });
        toast.success("Task updated");
      }
      setOpen(false);
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Something went wrong. Please try again.",
      );
      setPending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) reset();
      }}
    >
      <DialogTrigger render={trigger as ReactElement} />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? "New task" : "Edit task"}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div
              role="alert"
              className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
            >
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="task-title">Title</Label>
            <Input
              id="task-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Follow up with the buyer"
              maxLength={200}
              autoFocus
              disabled={pending}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="task-desc">Description</Label>
            <Textarea
              id="task-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Anything worth remembering about this task…"
              rows={3}
              disabled={pending}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <Label>Priority</Label>
              <Select
                value={priority}
                onValueChange={(v) => v && setPriority(v as TaskPriority)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRIORITIES.map((p) => (
                    <SelectItem key={p} value={p} className="capitalize">
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Status</Label>
              <Select
                value={status}
                onValueChange={(v) => v && setStatus(v as OpenStatus)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {OPEN_STATES.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="task-due">Due</Label>
              <Input
                id="task-due"
                type="datetime-local"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
                disabled={pending}
              />
            </div>
          </div>

          <DialogFooter>
            <DialogClose
              render={
                <Button type="button" variant="ghost" disabled={pending}>
                  Cancel
                </Button>
              }
            />
            <Button type="submit" disabled={pending}>
              {pending && <Loader2 className="size-4 animate-spin" />}
              {mode === "create" ? "Create task" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
