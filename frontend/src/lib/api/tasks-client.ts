"use client";

import { apiRequest } from "@/lib/api/client";
import type { Task, TaskInput, TaskUpdateInput } from "@/lib/api/types";

/**
 * Task mutations from the browser, through the same-origin BFF proxy (session
 * cookie + CSRF attached there).
 *
 * Completion is deliberately its own call, not a PATCH to `status: "done"`:
 * the API stamps `completed_at`, writes the completion onto the linked record's
 * timeline and audits it. `reopen` undoes that.
 */

export async function createTask(input: TaskInput): Promise<Task> {
  return apiRequest<Task>("/tasks", { method: "POST", body: input });
}

export async function updateTask(
  id: string,
  input: TaskUpdateInput,
): Promise<Task> {
  return apiRequest<Task>(`/tasks/${id}`, { method: "PATCH", body: input });
}

export async function completeTask(id: string, note?: string): Promise<Task> {
  return apiRequest<Task>(`/tasks/${id}/complete`, {
    method: "POST",
    body: note ? { note } : {},
  });
}

export async function reopenTask(id: string): Promise<Task> {
  return apiRequest<Task>(`/tasks/${id}/reopen`, { method: "POST" });
}

export async function deleteTask(id: string): Promise<void> {
  return apiRequest<void>(`/tasks/${id}`, { method: "DELETE" });
}
