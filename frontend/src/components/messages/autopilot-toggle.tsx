"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { Bot, Hand, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/i18n/language-provider";
import { setConversationAutopilot } from "@/lib/api/conversations-client";

/**
 * Autopilot status + takeover control for a thread.
 *
 * On autopilot the AI agent answers the client 24/7; "take over" flips the
 * conversation to manual and the agent falls silent, leaving the manager to
 * reply by hand. The state is authoritative on the server — the button mutates
 * it and refreshes rather than holding an optimistic copy, so what shows always
 * matches what the agent reads.
 */
export function AutopilotToggle({
  conversationId,
  autopilot,
}: {
  conversationId: string;
  autopilot: boolean;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const [pending, startTransition] = useTransition();

  function toggle() {
    startTransition(async () => {
      await setConversationAutopilot(conversationId, !autopilot);
      router.refresh();
    });
  }

  return (
    <div className="flex items-center gap-2">
      {autopilot ? (
        <Badge className="gap-1">
          <Bot className="size-3" />
          {t("body.apilotOn")}
        </Badge>
      ) : (
        <Badge variant="outline" className="gap-1">
          <Hand className="size-3" />
          {t("body.apilotManual")}
        </Badge>
      )}
      <Button variant="outline" size="sm" onClick={toggle} disabled={pending}>
        {pending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : autopilot ? (
          <Hand className="size-4" />
        ) : (
          <Bot className="size-4" />
        )}
        {autopilot ? t("body.apilotTakeOver") : t("body.apilotHandBack")}
      </Button>
    </div>
  );
}
