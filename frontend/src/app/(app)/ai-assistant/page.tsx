import type { Metadata } from "next";
import { Sparkles } from "lucide-react";

import { AssistantChat } from "@/components/ai/assistant-chat";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { getAiStatus, listConversations } from "@/lib/api/ai";
import { requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "AI Assistant" };

export default async function AiAssistantPage() {
  const session = await requireSession();
  const [status, conversations] = await Promise.all([
    getAiStatus(),
    listConversations().catch(() => []),
  ]);

  // The status call is the gate: a workspace with the layer off, or a user
  // without `ai.use`, gets an explanation rather than a chat box that would
  // fail on first use. This mirrors the server's own refusal — the UI never
  // offers an affordance the API would reject.
  if (!status.status.enabled || !status.status.can_use) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="AI Assistant"
          description="Ask questions about your pipeline in plain language."
        />
        <EmptyState
          icon={Sparkles}
          title={
            status.status.enabled
              ? "You do not have access to the assistant"
              : "The assistant is not enabled"
          }
          description={
            status.status.enabled
              ? "Ask an administrator to grant you AI access."
              : "An administrator can turn the AI layer on for this workspace."
          }
        />
      </div>
    );
  }

  return (
    <AssistantChat
      user={session}
      initialConversations={conversations}
      budget={status.budget}
      model={status.status.model}
    />
  );
}
