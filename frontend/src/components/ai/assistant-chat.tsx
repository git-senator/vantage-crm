"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { BrandMark } from "@/components/shared/brand";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "@/i18n/language-provider";
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
 *
 * **The reply is revealed as if typed.** The whole answer arrives at once from
 * the server, but it is unveiled word by word, with a pause on the animated
 * dots first — the broker "reads, then writes". This is presentation only: the
 * message, its cost, and its record are already final the moment it arrives;
 * the animation cannot change or lose a character of it.
 */

const SUGGESTION_KEYS = [
  "body.aiSuggest1",
  "body.aiSuggest2",
  "body.aiSuggest3",
  "body.aiSuggest4",
];

type PendingMessage = AiMessage & { pending?: boolean };

/** An assistant reply being unveiled word by word. */
type Typing = { message: PendingMessage; full: string; shown: number };

//: How long a word takes to "type". Base cadence plus a human pause after
//: sentence- and clause-ending punctuation, so the reveal breathes where a
//: person would. Jitter keeps it from feeling metronomic.
function wordDelay(justRevealed: string): number {
  const last = justRevealed.trimEnd().slice(-1);
  const base = 34 + Math.random() * 26; // ~34–60ms between words
  if (last === "." || last === "!" || last === "?") return base + 280;
  if (last === "," || last === ";" || last === ":") return base + 130;
  return base;
}

//: Advance the reveal to the end of the next word (the run of non-spaces plus
//: the spaces after it). Long replies step by several words so a page of text
//: does not take a full minute to unveil.
function nextBoundary(full: string, from: number): number {
  const words = full.length - from > 900 ? 3 : 1;
  let i = from;
  for (let w = 0; w < words && i < full.length; w += 1) {
    while (i < full.length && full[i] === " ") i += 1;
    while (i < full.length && full[i] !== " ") i += 1;
    while (i < full.length && full[i] === " ") i += 1;
  }
  return i;
}

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
  const { t } = useTranslation();
  const [conversations, setConversations] =
    useState<AiConversation[]>(initialConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<PendingMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  // The reply currently being unveiled, or null when nothing is being typed.
  const [typing, setTyping] = useState<Typing | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // A monotonic counter for optimistic ids — mutated only in event handlers, so
  // it stays out of render and needs no impure clock or randomness.
  const pendingSeq = useRef(0);

  // Busy whenever we are waiting on the server *or* still unveiling a reply, so
  // the composer stays disabled until the broker has "finished writing".
  const busy = sending || typing !== null;

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, typing]);

  // The typewriter. Re-runs on every reveal step: schedule the next word, and
  // when the whole reply is shown, commit it to the message list and stop.
  // Every state change is deferred into a timeout — never a synchronous
  // setState in the effect body — so a long reply cannot cascade renders.
  useEffect(() => {
    if (typing === null) return;
    if (typing.shown >= typing.full.length) {
      // A short beat after the last word — the caret lingers, then the finished
      // message settles into the transcript. Feels like a person hitting send.
      const finished = typing.message;
      const timer = setTimeout(() => {
        setMessages((current) => [...current, finished]);
        setTyping(null);
      }, 350);
      return () => clearTimeout(timer);
    }
    const next = nextBoundary(typing.full, typing.shown);
    const revealed = typing.full.slice(typing.shown, next);
    const timer = setTimeout(
      () => setTyping((t) => (t ? { ...t, shown: next } : t)),
      wordDelay(revealed),
    );
    return () => clearTimeout(timer);
  }, [typing]);

  async function openConversation(id: string) {
    setActiveId(id);
    setLoading(true);
    setTyping(null);
    try {
      const detail = await getConversation(id);
      setMessages(detail.messages);
    } catch {
      toast.error(t("body.aiOpenError"));
    } finally {
      setLoading(false);
    }
  }

  function startNew() {
    setActiveId(null);
    setMessages([]);
    setDraft("");
    setTyping(null);
  }

  async function submit(text: string) {
    const message = text.trim();
    if (!message || busy) return;
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
      // Settle the user's turn, then hand the reply to the typewriter instead of
      // dropping it in whole — it appears as if the broker is writing it back.
      setMessages((current) => [
        ...current.filter((m) => m.id !== optimistic.id),
        { ...optimistic, id: `${optimistic.id}-final` },
      ]);
      setTyping({ message: reply, full: reply.content ?? "", shown: 0 });
    } catch (error) {
      // Keep the user's message on screen; the server kept it too. Surface the
      // server's own words — a budget refusal explains when it resets.
      setMessages((current) =>
        current.filter((m) => m.id !== optimistic.id).concat(optimistic),
      );
      toast.error(
        error instanceof Error ? error.message : t("body.aiReplyError"),
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
      toast.error(t("body.aiDeleteError"));
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.aiTitle")}
        description={t("body.aiChatDesc", { model })}
        actions={
          <Button onClick={startNew} variant="outline">
            <Plus className="size-4" />
            {t("body.aiNewChat")}
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
            {messages.length === 0 && !loading && !typing ? (
              <div className="flex h-full flex-col items-center justify-center gap-5 text-center">
                <BrandMark className="size-11" />
                <div className="max-w-md space-y-1">
                  <p className="text-sm font-medium">{t("body.aiHowHelp")}</p>
                  <p className="text-xs text-muted-foreground">
                    {t("body.aiIntro")}
                  </p>
                </div>
                <div className="grid w-full max-w-md gap-2">
                  {SUGGESTION_KEYS.map((key) => {
                    const suggestion = t(key);
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => submit(suggestion)}
                        className="rounded-lg border px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted"
                      >
                        {suggestion}
                      </button>
                    );
                  })}
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

            {/* The reply mid-typewriter: revealed text with a blinking caret. */}
            {typing && typing.shown > 0 ? (
              <div className="flex gap-3">
                <BrandMark className="mt-0.5 size-8 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="whitespace-pre-wrap text-sm leading-relaxed">
                    {typing.full.slice(0, typing.shown)}
                    <span className="ml-0.5 inline-block h-4 w-px translate-y-0.5 animate-pulse bg-foreground/70 align-middle" />
                  </div>
                </div>
              </div>
            ) : null}

            {/* Before the first word: the "broker is typing" dots. */}
            {sending || (typing && typing.shown === 0) ? (
              <div className="flex gap-3">
                <BrandMark className="mt-0.5 size-8 shrink-0" />
                <TypingDots />
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
                placeholder={t("body.aiInputPlaceholder")}
                rows={1}
                className="max-h-40 min-h-[42px] resize-none"
                disabled={busy}
              />
              <Button
                type="submit"
                size="icon"
                disabled={busy || !draft.trim()}
                className="size-[42px] shrink-0"
              >
                {busy ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <ArrowUp className="size-4" />
                )}
              </Button>
            </form>
            {budget.exhausted ? (
              <p className="mt-2 text-xs text-destructive">
                {t("body.aiBudgetExhausted")}
              </p>
            ) : null}
          </div>
        </Card>

        {/* ------------------------------------------------ history */}
        <div className="space-y-3">
          <p className="px-1 text-xs font-medium text-muted-foreground">
            {t("body.aiRecentChats")}
          </p>
          {conversations.length === 0 ? (
            <p className="px-1 text-xs text-muted-foreground">
              {t("body.aiNoConversations")}
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
                    {conversation.title ?? t("body.aiNewChat")}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(conversation.id)}
                    className="mr-1 shrink-0 rounded p-1.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                    aria-label={t("body.aiDeleteConversation")}
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

/** Three dots that rise in sequence — the "someone is typing" cue. */
function TypingDots() {
  return (
    <div className="flex items-center gap-1 py-2" aria-label="Assistant is typing">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60"
          style={{ animationDelay: `${i * 160}ms`, animationDuration: "1s" }}
        />
      ))}
    </div>
  );
}
