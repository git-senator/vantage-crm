"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  Conversation,
  ConversationMessage,
  MessageSendInput,
} from "@/lib/api/types";

/**
 * Sending returns a **queued** message, not a sent one.
 *
 * The API records it and delivers afterwards, so a UI that reported "sent"
 * here would be claiming something it does not know. Show it as pending and let
 * the refreshed thread tell the truth.
 */
export async function sendMessage(
  input: MessageSendInput,
): Promise<ConversationMessage> {
  return apiRequest<ConversationMessage>("/conversations/messages", {
    method: "POST",
    body: input,
  });
}

export async function markConversationRead(
  id: string,
): Promise<Conversation> {
  return apiRequest<Conversation>(`/conversations/${id}/read`, {
    method: "POST",
    body: {},
  });
}

export async function fileConversation(
  id: string,
  input: { entity_type?: string | null; entity_id?: string | null; is_pinned?: boolean },
): Promise<Conversation> {
  return apiRequest<Conversation>(`/conversations/${id}`, {
    method: "PATCH",
    body: input,
  });
}
