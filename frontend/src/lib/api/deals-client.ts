"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  Deal,
  DealInput,
  DealStageTransitionInput,
  Pipeline,
} from "@/lib/api/types";

/**
 * Deal and pipeline mutations from the browser.
 *
 * Goes through the same-origin BFF proxy, which attaches the session cookie
 * and verifies CSRF. Nothing here handles a token.
 */

export async function createDeal(input: DealInput): Promise<Deal> {
  return apiRequest<Deal>("/deals", { method: "POST", body: input });
}

export async function updateDeal(
  id: string,
  input: Partial<DealInput>,
): Promise<Deal> {
  return apiRequest<Deal>(`/deals/${id}`, { method: "PATCH", body: input });
}

export async function deleteDeal(id: string): Promise<void> {
  return apiRequest<void>(`/deals/${id}`, { method: "DELETE" });
}

/**
 * Move a deal to another stage.
 *
 * Deliberately not part of `updateDeal`: the server writes stage history,
 * resets probability and sets or clears the close date as one atomic action.
 * A PATCH that could set `stage_id` would let a client do the first part
 * without the rest.
 *
 * Throws 409 when the deal is already in that stage, when the stage belongs to
 * another pipeline, or when marking a deal lost without a reason.
 */
export async function moveDealStage(
  id: string,
  input: DealStageTransitionInput,
): Promise<Deal> {
  return apiRequest<Deal>(`/deals/${id}/stage`, {
    method: "POST",
    body: input,
  });
}

export async function assignDeal(id: string, ownerId: string): Promise<Deal> {
  return apiRequest<Deal>(`/deals/${id}/assign`, {
    method: "POST",
    body: { owner_id: ownerId },
  });
}

// ------------------------------------------------------------- pipelines

export async function updatePipeline(
  id: string,
  input: { name?: string; description?: string | null; is_default?: boolean },
): Promise<Pipeline> {
  return apiRequest<Pipeline>(`/pipelines/${id}`, {
    method: "PATCH",
    body: input,
  });
}

export async function addPipelineStage(
  pipelineId: string,
  input: {
    key: string;
    name: string;
    position?: number;
    default_probability?: number;
    is_won?: boolean;
    is_lost?: boolean;
  },
): Promise<Pipeline> {
  return apiRequest<Pipeline>(`/pipelines/${pipelineId}/stages`, {
    method: "POST",
    body: input,
  });
}

export async function updatePipelineStage(
  pipelineId: string,
  stageId: string,
  input: {
    name?: string;
    position?: number;
    default_probability?: number;
  },
): Promise<Pipeline> {
  return apiRequest<Pipeline>(`/pipelines/${pipelineId}/stages/${stageId}`, {
    method: "PATCH",
    body: input,
  });
}

/** 409 while the stage still holds deals, or if it is the last one. */
export async function deletePipelineStage(
  pipelineId: string,
  stageId: string,
): Promise<Pipeline> {
  return apiRequest<Pipeline>(`/pipelines/${pipelineId}/stages/${stageId}`, {
    method: "DELETE",
  });
}
