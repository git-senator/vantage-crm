"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ClientApiError } from "@/lib/api/client";
import { sendMessage } from "@/lib/api/conversations-client";

/**
 * Reply composer.
 *
 * Addresses the thread rather than a conversation id, matching the API: the
 * server finds or creates the thread, so a double-click cannot race itself into
 * two conversations with the same person.
 *
 * The subject defaults to `Re: <thread subject>` and stays editable. Sending
 * clears the box and refreshes the route — the message comes back as `queued`,
 * and the thread tells the truth about delivery a moment later.
 */
export function MessageComposer({
  toAddress,
  toName,
  subject,
}: {
  toAddress: string;
  toName: string | null;
  subject: string | null;
}) {
  const router = useRouter();
  const [body, setBody] = useState("");
  const [subjectLine, setSubjectLine] = useState(
    subject && !subject.toLowerCase().startsWith("re:")
      ? `Re: ${subject}`
      : (subject ?? ""),
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!body.trim()) return;
    setPending(true);
    setError(null);
    try {
      await sendMessage({
        to_address: toAddress,
        to_name: toName,
        subject: subjectLine.trim() || "(no subject)",
        body_text: body,
      });
      setBody("");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Could not send that message.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-2 border-t p-3">
      <Input
        value={subjectLine}
        onChange={(event) => setSubjectLine(event.target.value)}
        placeholder="Subject"
        aria-label="Subject"
        disabled={pending}
      />
      <Textarea
        value={body}
        onChange={(event) => setBody(event.target.value)}
        placeholder={`Reply to ${toName ?? toAddress}…`}
        aria-label="Message"
        rows={3}
        disabled={pending}
        onKeyDown={(event) => {
          // Ctrl/Cmd+Enter sends. Plain Enter must not: these are paragraphs of
          // client correspondence, not chat lines.
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            void submit();
          }
        }}
      />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">
          Sending to {toAddress}
        </span>
        <Button size="sm" onClick={submit} disabled={pending || !body.trim()}>
          {pending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
          Send
        </Button>
      </div>
    </div>
  );
}
