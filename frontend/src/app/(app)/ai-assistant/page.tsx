import type { Metadata } from "next";
import { Sparkles } from "lucide-react";

import { AssistantChat } from "@/components/ai/assistant-chat";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { getAiStatus, listConversations } from "@/lib/api/ai";
import { getTranslations } from "@/i18n/server";
import { requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "AI Assistant" };

export default async function AiAssistantPage() {
  const session = await requireSession();
  const [status, conversations, t] = await Promise.all([
    getAiStatus(),
    listConversations().catch(() => []),
    getTranslations(),
  ]);

  // The status call is the gate: a workspace with the layer off, or a user
  // without `ai.use`, gets an explanation rather than a chat box that would
  // fail on first use. This mirrors the server's own refusal — the UI never
  // offers an affordance the API would reject.
  if (!status.status.enabled || !status.status.can_use) {
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("body.aiTitle")}
          description={t("body.aiDesc")}
        />
        <EmptyState
          icon={Sparkles}
          title={
            status.status.enabled
              ? t("body.aiNoAccess")
              : t("body.aiDisabled")
          }
          description={
            status.status.enabled
              ? t("body.aiNoAccessDesc")
              : t("body.aiDisabledDesc")
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
