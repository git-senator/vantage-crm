import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type {
  Conversation,
  ConversationDetail,
  Page,
} from "@/lib/api/types";

/** The shared inbox, most recently active first. */
export async function listConversations(params?: {
  channel?: string;
  entity_type?: string;
  entity_id?: string;
  unread_only?: boolean;
  search?: string;
  limit?: number;
}): Promise<Page<Conversation>> {
  return apiFetch<Page<Conversation>>(`/conversations${toQuery(params ?? {})}`);
}

/** One thread and its messages, oldest first. */
export async function getConversation(id: string): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`/conversations/${id}`);
}
