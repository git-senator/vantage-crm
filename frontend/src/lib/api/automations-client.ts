"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowRun,
  WorkflowSummary,
  WorkflowValidation,
  WorkflowVersion,
} from "@/lib/api/types";

export async function createWorkflow(input: {
  name: string;
  trigger_type: string;
  description?: string | null;
}): Promise<WorkflowDetail> {
  return apiRequest<WorkflowDetail>("/automations", {
    method: "POST",
    body: input,
  });
}

/**
 * Save the builder's current state.
 *
 * Not validated server-side — a draft is allowed to be incoherent, which is
 * what lets the builder save on every change instead of only when the graph
 * happens to be complete.
 */
export async function saveDefinition(
  id: string,
  definition: WorkflowDefinition,
): Promise<WorkflowVersion> {
  return apiRequest<WorkflowVersion>(`/automations/${id}/definition`, {
    method: "PUT",
    body: { definition },
  });
}

/** Check without saving. Returns every problem, so the canvas marks up once. */
export async function validateDefinition(
  id: string,
  definition: WorkflowDefinition,
): Promise<WorkflowValidation> {
  return apiRequest<WorkflowValidation>(`/automations/${id}/validate`, {
    method: "POST",
    body: { definition },
  });
}

export async function publishWorkflow(id: string): Promise<WorkflowVersion> {
  return apiRequest<WorkflowVersion>(`/automations/${id}/publish`, {
    method: "POST",
    body: {},
  });
}

export async function setWorkflowEnabled(
  id: string,
  isEnabled: boolean,
): Promise<WorkflowSummary> {
  return apiRequest<WorkflowSummary>(`/automations/${id}/enabled`, {
    method: "POST",
    body: { is_enabled: isEnabled },
  });
}

export async function renameWorkflow(
  id: string,
  input: { name?: string; description?: string | null },
): Promise<WorkflowSummary> {
  return apiRequest<WorkflowSummary>(`/automations/${id}`, {
    method: "PATCH",
    body: input,
  });
}

export async function deleteWorkflow(id: string): Promise<void> {
  await apiRequest(`/automations/${id}`, { method: "DELETE" });
}

export async function cancelRun(id: string): Promise<WorkflowRun> {
  return apiRequest<WorkflowRun>(`/automations/runs/${id}/cancel`, {
    method: "POST",
    body: {},
  });
}
