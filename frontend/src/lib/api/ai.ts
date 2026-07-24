import "server-only";

import { apiFetch } from "@/lib/api/server";
import type {
  AiConversation,
  AiConversationDetail,
  AiStatusResponse,
} from "@/lib/api/types";

/**
 * AI reads for server components.
 *
 * The status call is what the page uses to decide whether to render the
 * assistant at all: a workspace with the layer off, or a user without `ai.use`,
 * gets an explanatory empty state rather than a chat box that would fail on
 * first use.
 */

export async function getAiStatus(): Promise<AiStatusResponse> {
  return apiFetch<AiStatusResponse>("/ai/status");
}

export async function listConversations(): Promise<AiConversation[]> {
  return apiFetch<AiConversation[]>("/ai/conversations");
}

export async function getConversation(
  id: string,
): Promise<AiConversationDetail> {
  return apiFetch<AiConversationDetail>(`/ai/conversations/${id}`);
}
