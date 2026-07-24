"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { BrandMark } from "@/components/shared/brand";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  createConversation,
  deleteConversation,
  getConversation,
  sendMessage,
} from "@/lib/api/ai-client";
import type {
  AiBudget,
  AiConversation,
  AiMessage,
  UserProfile,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The assistant chat.
 *
 * A thin client over the API: it never composes a prompt or reasons about
 * context — the server does all of that under the user's scope. This component
 * owns conversation selection, optimistic rendering of the user's turn, and
 * surfacing whatever the server says (including a budget refusal, which the API
 * returns as a plain error message the user can read).
 *
 * The user's message is shown immediately; the reply is awaited. The server
 * commits the user's turn before it dispatches, so a failed reply leaves the
 * message in place with an error rather than losing it.
 */

const SUGGESTIONS = [
  "Which five leads should I call first this morning, and why?",
  "Summarise the state of my pipeline this week.",
  "Draft a follow-up to a client after a second showing.",
  "What deals are most at risk of slipping this month?",
];

type PendingMessage = AiMessage & { pending?: boolean };

export function AssistantChat({
  user,
  initialConversations,
  budget,
  model,
}: {
  user: UserProfile;
  initialConversations: AiConversation[];
  budget: AiBudget;
  model: string;
}) {
  const [conversations, setConversations] =
    useState<AiConversation[]>(initialConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<PendingMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // A monotonic counter for optimistic ids — mutated only in event handlers, so
  // it stays out of render and needs no impure clock or randomness.
  const pendingSeq = useRef(0);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  async function openConversation(id: string) {
    setActiveId(id);
    setLoading(true);
    try {
      const detail = await getConversation(id);
      setMessages(detail.messages);
    } catch {
      toast.error("Could not open that conversation.");
    } finally {
      setLoading(false);
    }
  }

  function startNew() {
    setActiveId(null);
    setMessages([]);
    setDraft("");
  }

  async function submit(text: string) {
    const message = text.trim();
    if (!message || sending) return;
    setSending(true);
    setDraft("");

    // Show the user's turn at once. If nothing exists yet, create a conversation
    // first — the server anchors and titles it.
    const optimistic: PendingMessage = {
      id: `pending-${(pendingSeq.current += 1)}`,
      role: "user",
      content: message,
      prompt_tokens: null,
      completion_tokens: null,
      truncated: false,
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);

    try {
      let conversationId = activeId;
      if (conversationId === null) {
        const created = await createConversation({});
        conversationId = created.id;
        setActiveId(created.id);
        setConversations((current) => [created, ...current]);
      }

      const reply = await sendMessage(conversationId, message);
      setMessages((current) => [
        ...current.filter((m) => m.id !== optimistic.id),
        { ...optimistic, id: `${optimistic.id}-final` },
        reply,
      ]);
    } catch (error) {
      // Keep the user's message on screen; the server kept it too. Surface the
      // server's own words — a budget refusal explains when it resets.
      setMessages((current) =>
        current.filter((m) => m.id !== optimistic.id).concat(optimistic),
      );
      toast.error(
        error instanceof Error ? error.message : "The assistant could not reply.",
      );
    } finally {
      setSending(false);
    }
  }

  async function remove(id: string) {
    try {
      await deleteConversation(id);
      setConversations((current) => current.filter((c) => c.id !== id));
      if (activeId === id) startNew();
    } catch {
      toast.error("Could not delete that conversation.");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Assistant"
        description={`Ask about your pipeline in plain language. Model: ${model}.`}
        actions={
          <Button onClick={startNew} variant="outline">
            <Plus className="size-4" />
            New chat
          </Button>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_280px]">
        {/* ---------------------------------------------------- chat */}
        <Card className="flex min-h-[620px] flex-col gap-0 p-0">
          <div
            ref={scrollRef}
            className="scrollbar-slim flex-1 space-y-6 overflow-y-auto p-5"
          >
            {messages.length === 0 && !loading ? (
              <div className="flex h-full flex-col items-center justify-center gap-5 text-center">
                <BrandMark className="size-11" />
                <div className="max-w-md space-y-1">
                  <p className="text-sm font-medium">How can I help?</p>
                  <p className="text-xs text-muted-foreground">
                    I work from your CRM data, within what you can see. I can
                    summarise, prioritise and draft — I can&apos;t change records
                    or send anything.
                  </p>
                </div>
                <div className="grid w-full max-w-md gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => submit(suggestion)}
                      className="rounded-lg border px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            {loading ? (
              <div className="flex justify-center py-10">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : null}

            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end gap-3">
                  <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">
                    {message.content}
                  </div>
                  <span
                    className="mt-auto grid size-8 shrink-0 place-items-center rounded-full text-[11px] font-medium"
                    style={{
                      backgroundColor: `oklch(0.92 0.055 ${user.avatar_hue})`,
                      color: `oklch(0.42 0.13 ${user.avatar_hue})`,
                    }}
                  >
                    {user.initials}
                  </span>
                </div>
              ) : (
                <div key={message.id} className="flex gap-3">
                  <BrandMark className="mt-0.5 size-8 shrink-0" />
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="whitespace-pre-wrap text-sm leading-relaxed">
                      {message.content}
                    </div>
                    {message.truncated ? (
                      <p className="text-xs text-muted-foreground">
                        The reply was cut short at the length limit.
                      </p>
                    ) : null}
                  </div>
                </div>
              ),
            )}

            {sending ? (
              <div className="flex gap-3">
                <BrandMark className="mt-0.5 size-8 shrink-0" />
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Thinking…
                </div>
              </div>
            ) : null}
          </div>

          <div className="border-t p-3">
            <form
              onSubmit={(event) => {
                event.preventDefault();
                submit(draft);
              }}
              className="flex items-end gap-2"
            >
              <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit(draft);
                  }
                }}
                placeholder="Ask about a lead, a deal, your pipeline…"
                rows={1}
                className="max-h-40 min-h-[42px] resize-none"
                disabled={sending}
              />
              <Button
                type="submit"
                size="icon"
                disabled={sending || !draft.trim()}
                className="size-[42px] shrink-0"
              >
                {sending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <ArrowUp className="size-4" />
                )}
              </Button>
            </form>
            {budget.exhausted ? (
              <p className="mt-2 text-xs text-destructive">
                This workspace has reached its monthly AI budget. It resets at the
                start of next month.
              </p>
            ) : null}
          </div>
        </Card>

        {/* ------------------------------------------------ history */}
        <div className="space-y-3">
          <p className="px-1 text-xs font-medium text-muted-foreground">
            Recent chats
          </p>
          {conversations.length === 0 ? (
            <p className="px-1 text-xs text-muted-foreground">
              Your conversations will appear here.
            </p>
          ) : (
            <div className="space-y-1">
              {conversations.map((conversation) => (
                <div
                  key={conversation.id}
                  className={cn(
                    "group flex items-center gap-1 rounded-lg text-sm transition-colors",
                    activeId === conversation.id
                      ? "bg-muted"
                      : "hover:bg-muted/60",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => openConversation(conversation.id)}
                    className="min-w-0 flex-1 truncate px-3 py-2 text-left"
                  >
                    <Sparkles className="mr-1.5 inline size-3 text-muted-foreground" />
                    {conversation.title ?? "New chat"}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(conversation.id)}
                    className="mr-1 shrink-0 rounded p-1.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                    aria-label="Delete conversation"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
