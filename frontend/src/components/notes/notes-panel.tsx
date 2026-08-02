"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2, Pencil, Pin, PinOff, Trash2 } from "lucide-react";

import { OwnerAvatar } from "@/components/shared/owner-avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "@/i18n/language-provider";
import { ClientApiError } from "@/lib/api/client";
import {
  createNote,
  deleteNote,
  updateNote,
} from "@/lib/api/notes-client";
import type { Note, RecordEntityType } from "@/lib/api/types";

/**
 * Notes on a record: add, edit, pin, delete. Data is fetched by the server
 * component and passed as `notes`; every mutation goes through the BFF and then
 * `router.refresh()`, so the panel re-renders from the authoritative server
 * state rather than a hand-maintained local copy.
 *
 * Rich text is markdown, kept as plain text here (whitespace preserved) — the
 * body is never rendered as HTML, which is the safe default the API assumes.
 */
export function NotesPanel({
  entityType,
  entityId,
  notes,
  canManage,
}: {
  entityType: RecordEntityType;
  entityId: string;
  notes: Note[];
  canManage: boolean;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const [body, setBody] = useState("");
  const [pinned, setPinned] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setPending(true);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError ? caught.message : t("body.errGeneric"),
      );
    } finally {
      setPending(false);
    }
  }

  async function handleAdd() {
    const trimmed = body.trim();
    if (!trimmed) return;
    await run(async () => {
      await createNote({
        entity_type: entityType,
        entity_id: entityId,
        body: trimmed,
        is_pinned: pinned,
      });
      setBody("");
      setPinned(false);
    });
  }

  return (
    <div className="space-y-4">
      {canManage && (
        <div className="space-y-2">
          <Textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder={t("body.npPlaceholder")}
            rows={3}
            disabled={pending}
          />
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={pinned}
                onChange={(event) => setPinned(event.target.checked)}
                disabled={pending}
              />
              {t("body.npPinToTop")}
            </label>
            <Button size="sm" onClick={handleAdd} disabled={pending || !body.trim()}>
              {pending && <Loader2 className="size-4 animate-spin" />}
              {t("body.npAddNote")}
            </Button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {notes.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("body.npNoNotes")}</p>
      ) : (
        <ul className="space-y-3">
          {notes.map((note) => (
            <NoteCard
              key={note.id}
              note={note}
              pending={pending}
              onDelete={() => run(() => deleteNote(note.id))}
              onTogglePin={() =>
                run(() => updateNote(note.id, { is_pinned: !note.is_pinned }))
              }
              onSave={(nextBody) =>
                run(() => updateNote(note.id, { body: nextBody }))
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function NoteCard({
  note,
  pending,
  onDelete,
  onTogglePin,
  onSave,
}: {
  note: Note;
  pending: boolean;
  onDelete: () => void;
  onTogglePin: () => void;
  onSave: (body: string) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.body);

  return (
    <li className="rounded-lg border bg-card p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <OwnerAvatar owner={note.author} size="xs" />
          <div className="leading-tight">
            <p className="text-xs font-medium">
              {note.author?.full_name ?? t("body.npUnknown")}
            </p>
            {/* Rendered in the viewer's locale/timezone, which differs from the
                server's — suppress the unavoidable hydration text mismatch so
                the client value wins without a console error (React #418). */}
            <p
              className="text-[11px] text-muted-foreground"
              suppressHydrationWarning
            >
              {new Date(note.created_at).toLocaleString()}
            </p>
          </div>
          {note.is_pinned && (
            <Badge variant="secondary" className="ml-1">
              <Pin className="size-3" /> {t("body.npPinned")}
            </Badge>
          )}
        </div>

        {note.is_own && !editing && (
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onTogglePin}
              disabled={pending}
              title={note.is_pinned ? t("body.npUnpin") : t("body.npPin")}
            >
              {note.is_pinned ? (
                <PinOff className="size-4" />
              ) : (
                <Pin className="size-4" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => {
                setDraft(note.body);
                setEditing(true);
              }}
              disabled={pending}
              title={t("buttons.edit")}
            >
              <Pencil className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              className="text-destructive"
              onClick={onDelete}
              disabled={pending}
              title={t("buttons.delete")}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        )}
      </div>

      {editing ? (
        <div className="mt-2 space-y-2">
          <Textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={3}
            disabled={pending}
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(false)}
              disabled={pending}
            >
              {t("buttons.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                onSave(draft.trim());
                setEditing(false);
              }}
              disabled={pending || !draft.trim()}
            >
              {t("buttons.save")}
            </Button>
          </div>
        </div>
      ) : (
        <>
          {note.title && (
            <p className="mt-2 text-sm font-medium">{note.title}</p>
          )}
          <p className="mt-1 text-sm whitespace-pre-wrap">{note.body}</p>
        </>
      )}
    </li>
  );
}
