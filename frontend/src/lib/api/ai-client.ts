"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  AiAnchorEntity,
  AiConversation,
  AiConversationDetail,
  AiMessage,
} from "@/lib/api/types";

/**
 * Assistant mutations from the browser, through the same-origin BFF proxy.
 *
 * `sendMessage` is synchronous — the user is waiting for the answer, and the
 * server commits their message before it dispatches, so a failed reply never
 * loses what they typed. A budget refusal returns 429 and a provider fault 503;
 * the caller surfaces the message rather than swallowing it.
 */

export async function createConversation(input: {
  entity_type?: AiAnchorEntity;
  entity_id?: string;
}): Promise<AiConversation> {
  return apiRequest<AiConversation>("/ai/conversations", {
    method: "POST",
    body: input,
  });
}

export async function getConversation(
  id: string,
): Promise<AiConversationDetail> {
  return apiRequest<AiConversationDetail>(`/ai/conversations/${id}`);
}

export async function sendMessage(
  conversationId: string,
  text: string,
): Promise<AiMessage> {
  return apiRequest<AiMessage>(`/ai/conversations/${conversationId}/messages`, {
    method: "POST",
    body: { text },
  });
}

export async function deleteConversation(id: string): Promise<void> {
  await apiRequest<void>(`/ai/conversations/${id}`, { method: "DELETE" });
}
