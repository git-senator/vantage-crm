"use client";

import { apiRequest } from "@/lib/api/client";
import type { Note, NoteInput } from "@/lib/api/types";

/**
 * Note mutations from the browser, through the same-origin BFF proxy. Nothing
 * here handles a token; the proxy attaches the session cookie and verifies CSRF.
 */

export async function createNote(input: NoteInput): Promise<Note> {
  return apiRequest<Note>("/notes", { method: "POST", body: input });
}

export async function updateNote(
  id: string,
  input: Partial<Pick<NoteInput, "title" | "body" | "content_format" | "is_pinned">>,
): Promise<Note> {
  return apiRequest<Note>(`/notes/${id}`, { method: "PATCH", body: input });
}

export async function deleteNote(id: string): Promise<void> {
  return apiRequest<void>(`/notes/${id}`, { method: "DELETE" });
}
