"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import { useTranslation } from "@/i18n/language-provider";
import {
  cancelEvent,
  createEvent,
  deleteEvent,
  updateEvent,
} from "@/lib/api/calendar-client";
import type {
  CalendarEvent,
  CalendarEventInput,
  ScheduleConflict,
} from "@/lib/api/types";

const TYPES = [
  "showing",
  "call",
  "meeting",
  "closing",
  "open_house",
  "personal",
] as const;

const REMINDERS = [
  { value: "none", key: "body.calRemNone" },
  { value: "0", key: "body.calRemAtStart" },
  { value: "15", key: "body.calRem15" },
  { value: "30", key: "body.calRem30" },
  { value: "60", key: "body.calRem60" },
  { value: "1440", key: "body.calRem1440" },
] as const;

const ENTITY_TYPES = ["lead", "client", "deal", "property"] as const;

type EntityType = (typeof ENTITY_TYPES)[number];

export type LinkTarget = { id: string; label: string };
export type LinkOptions = Record<EntityType, LinkTarget[]>;

/** Where a linked record lives, since the plural is not always an added "s". */
const RECORD_PATH: Record<EntityType, string> = {
  lead: "leads",
  client: "clients",
  deal: "deals",
  property: "properties",
};

/**
 * `datetime-local` speaks wall clock, the API speaks instants. Both
 * conversions live here so a 15:00 showing does not drift by the timezone
 * offset on its way to the server and back.
 */
function toLocalInput(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fromLocalInput(local: string): string {
  return new Date(local).toISOString();
}

/**
 * A new event opens on the clicked day for an hour: at the next round hour if
 * that day is today, otherwise at ten, which is when a showing gets booked.
 */
function defaultRange(day: string | null): { start: string; end: string } {
  const now = new Date();
  const todayKey = toLocalInput(now.toISOString()).slice(0, 10);
  const base = day ? new Date(`${day}T00:00:00`) : new Date();
  base.setHours(!day || day === todayKey ? now.getHours() + 1 : 10, 0, 0, 0);
  const end = new Date(base.getTime() + 60 * 60 * 1000);
  return {
    start: toLocalInput(base.toISOString()),
    end: toLocalInput(end.toISOString()),
  };
}

/**
 * Create and edit, in one dialog.
 *
 * Cancelling and deleting sit side by side deliberately: the API prefers a
 * cancel, because "what was I meant to be doing on Tuesday" is a real
 * question and a deleted event takes its answer away. Delete asks twice.
 */
export function EventDialog({
  open,
  onOpenChange,
  event,
  day,
  links,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The event being edited, or null to create one. */
  event: CalendarEvent | null;
  /** The day that was clicked, when creating. */
  day: string | null;
  links: LinkOptions;
}) {
  const { t } = useTranslation();
  const router = useRouter();

  // Mounted fresh for each event the board opens (it keys this component), so
  // the initial values are the whole of the reset logic — no effect syncing
  // props into state behind the person's typing.
  const initial = useMemo(() => {
    if (event) {
      return {
        title: event.title,
        type: event.event_type as string,
        start: toLocalInput(event.starts_at),
        end: toLocalInput(event.ends_at),
        allDay: event.is_all_day,
        location: event.location ?? "",
        description: event.description ?? "",
        reminder:
          event.reminder_minutes === null ? "none" : String(event.reminder_minutes),
        entityType: event.entity_type ?? "none",
        entityId: event.entity_id ?? "",
        status: event.status as string,
      };
    }
    const range = defaultRange(day);
    return {
      title: "",
      type: "meeting",
      start: range.start,
      end: range.end,
      allDay: false,
      location: "",
      description: "",
      reminder: "none",
      entityType: "none",
      entityId: "",
      status: "confirmed",
    };
  }, [event, day]);

  const [title, setTitle] = useState(initial.title);
  const [type, setType] = useState(initial.type);
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [allDay, setAllDay] = useState(initial.allDay);
  const [location, setLocation] = useState(initial.location);
  const [description, setDescription] = useState(initial.description);
  const [reminder, setReminder] = useState(initial.reminder);
  const [entityType, setEntityType] = useState(initial.entityType);
  const [entityId, setEntityId] = useState(initial.entityId);
  const [status, setStatus] = useState(initial.status);

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<ScheduleConflict[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);


  const records = useMemo(
    () => (entityType === "none" ? [] : (links[entityType as EntityType] ?? [])),
    [entityType, links],
  );

  async function handleSubmit(submitted: FormEvent<HTMLFormElement>) {
    submitted.preventDefault();
    setError(null);
    setConflicts([]);

    if (!title.trim()) {
      setError(t("body.calEvNeedTitle"));
      return;
    }
    if (!start || !end || new Date(end) <= new Date(start)) {
      setError(t("body.calEvNeedEnd"));
      return;
    }

    // A link is both halves or neither; the API rejects half of one.
    const linked = entityType !== "none" && entityId !== "";
    const payload: CalendarEventInput = {
      title: title.trim(),
      description: description.trim() || null,
      location: location.trim() || null,
      event_type: type as CalendarEventInput["event_type"],
      status: status as CalendarEventInput["status"],
      starts_at: fromLocalInput(start),
      ends_at: fromLocalInput(end),
      is_all_day: allDay,
      reminder_minutes: reminder === "none" ? null : Number(reminder),
      entity_type: linked ? (entityType as EntityType) : null,
      entity_id: linked ? entityId : null,
    };

    setPending(true);
    try {
      const saved = event
        ? await updateEvent(event.id, payload)
        : await createEvent(payload);
      router.refresh();
      // Overlaps are reported, not refused. Hold the dialog open so they get
      // read rather than flashed past on the way to a closing window.
      if (saved.conflicts.length > 0) {
        setConflicts(saved.conflicts);
      } else {
        onOpenChange(false);
      }
    } catch {
      setError(t("body.calEvSaveFailed"));
    } finally {
      setPending(false);
    }
  }

  async function run(action: () => Promise<unknown>) {
    setPending(true);
    try {
      await action();
      router.refresh();
      onOpenChange(false);
    } catch {
      setError(t("body.calEvSaveFailed"));
    } finally {
      setPending(false);
    }
  }

  const recordHref =
    event?.entity_type && event.entity_id
      ? `/${RECORD_PATH[event.entity_type as EntityType]}/${event.entity_id}`
      : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {event ? t("body.calEditEvent") : t("body.calNewEvent")}
          </DialogTitle>
          {event?.status === "cancelled" && (
            <DialogDescription className="text-destructive">
              {t("body.calEvCancelled")}
            </DialogDescription>
          )}
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="ev-title">{t("body.calEvTitle")}</Label>
            <Input
              id="ev-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={pending}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="ev-type">{t("body.calEvType")}</Label>
              <Select value={type} onValueChange={(v) => setType(v ?? "meeting")}>
                <SelectTrigger id="ev-type" className="w-full">
                  {/* Base UI renders the raw value unless given a formatter. */}
                  <SelectValue>
                    {(value: string) => t(`body.calType_${value}`)}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {TYPES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`body.calType_${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ev-status">{t("body.calEvStatus")}</Label>
              <Select
                value={status}
                onValueChange={(v) => setStatus(v ?? "confirmed")}
              >
                <SelectTrigger id="ev-status" className="w-full">
                  <SelectValue>
                    {(value: string) => t(`body.calSt_${value}`)}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="confirmed">
                    {t("body.calSt_confirmed")}
                  </SelectItem>
                  <SelectItem value="tentative">
                    {t("body.calSt_tentative")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="ev-start">{t("body.calEvStart")}</Label>
              <Input
                id="ev-start"
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                disabled={pending}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="ev-end">{t("body.calEvEnd")}</Label>
              <Input
                id="ev-end"
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                disabled={pending}
              />
            </div>
          </div>

          <label className="flex w-fit items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={allDay}
              onChange={(e) => setAllDay(e.target.checked)}
              disabled={pending}
              className="size-4 rounded border-input"
            />
            {t("body.calEvAllDay")}
          </label>

          <div className="space-y-2">
            <Label htmlFor="ev-location">{t("body.calEvLocation")}</Label>
            <Input
              id="ev-location"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              disabled={pending}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="ev-reminder">{t("body.calEvReminder")}</Label>
            <Select
              value={reminder}
              onValueChange={(v) => setReminder(v ?? "none")}
            >
              <SelectTrigger id="ev-reminder" className="w-full">
                <SelectValue>
                  {(value: string) =>
                    t(
                      REMINDERS.find((option) => option.value === value)?.key ??
                        "body.calRemNone",
                    )
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {REMINDERS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {t(option.key)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="ev-entity">{t("body.calEvLinkType")}</Label>
              <Select
                value={entityType}
                onValueChange={(v) => {
                  setEntityType(v ?? "none");
                  setEntityId("");
                }}
              >
                <SelectTrigger id="ev-entity" className="w-full">
                  <SelectValue>
                    {(value: string) =>
                      value === "none"
                        ? t("body.calEvNoLink")
                        : t(`body.calEnt_${value}`)
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t("body.calEvNoLink")}</SelectItem>
                  {ENTITY_TYPES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`body.calEnt_${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {entityType !== "none" && (
              <div className="space-y-2">
                <Label htmlFor="ev-record">{t("body.calEvLinkRecord")}</Label>
                <Select
                  value={entityId}
                  onValueChange={(v) => setEntityId(v ?? "")}
                >
                  <SelectTrigger id="ev-record" className="w-full">
                    <SelectValue>
                      {(value: string) =>
                        records.find((record) => record.id === value)?.label ?? ""
                      }
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {records.map((record) => (
                      <SelectItem key={record.id} value={record.id}>
                        {record.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="ev-description">{t("body.calEvDescription")}</Label>
            <Textarea
              id="ev-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={pending}
            />
          </div>

          {error && (
            <p className="flex items-center gap-2 text-sm text-destructive">
              <TriangleAlert className="size-4 shrink-0" />
              {error}
            </p>
          )}

          {conflicts.length > 0 && (
            <div className="space-y-1 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
              <p className="font-medium">{t("body.calEvConflicts")}</p>
              <ul className="space-y-0.5 text-muted-foreground">
                {conflicts.map((conflict) => (
                  <li key={conflict.event_id}>
                    {conflict.title} — {new Date(conflict.starts_at).toLocaleString()}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-between">
            <div className="flex gap-2">
              {event && event.status !== "cancelled" && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => run(() => cancelEvent(event.id))}
                  disabled={pending}
                >
                  {t("body.calEvCancelIt")}
                </Button>
              )}
              {event && (
                <Button
                  type="button"
                  variant={confirmDelete ? "destructive" : "ghost"}
                  onClick={() =>
                    confirmDelete
                      ? run(() => deleteEvent(event.id))
                      : setConfirmDelete(true)
                  }
                  disabled={pending}
                  title={confirmDelete ? t("body.calEvDeleteAsk") : undefined}
                >
                  {t("body.calEvDelete")}
                </Button>
              )}
            </div>
            <div className="flex items-center gap-2">
              {recordHref && (
                <Button variant="ghost" render={<Link href={recordHref} />}>
                  {t("body.calEvOpenRecord")}
                </Button>
              )}
              <Button type="submit" disabled={pending}>
                {pending && <Loader2 className="size-4 animate-spin" />}
                {event ? t("body.calEvSave") : t("body.calEvCreate")}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
