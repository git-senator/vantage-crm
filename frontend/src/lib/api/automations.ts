import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type {
  Registries,
  WorkflowDetail,
  WorkflowRun,
  WorkflowRunDetail,
  WorkflowSummary,
} from "@/lib/api/types";

export async function listWorkflows(): Promise<WorkflowSummary[]> {
  return apiFetch<WorkflowSummary[]>("/automations");
}

export async function getWorkflow(id: string): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/automations/${id}`);
}

/**
 * The builder's palette.
 *
 * Fetched rather than hard-coded: two hand-maintained copies of "what fields
 * does the send-email action take" drift within a week, and the symptom is a
 * workflow that validates in the UI and fails at run time.
 */
export async function getRegistries(): Promise<Registries> {
  return apiFetch<Registries>("/automations/registries");
}

export async function listRuns(params?: {
  workflow_id?: string;
  limit?: number;
}): Promise<WorkflowRun[]> {
  return apiFetch<WorkflowRun[]>(`/automations/runs${toQuery(params ?? {})}`);
}

export async function getRun(id: string): Promise<WorkflowRunDetail> {
  return apiFetch<WorkflowRunDetail>(`/automations/runs/${id}`);
}
