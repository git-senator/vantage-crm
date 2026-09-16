import type { Metadata } from "next";
import Link from "next/link";
import {
  ExternalLink,
  Globe,
  Inbox,
  Mail,
  MessageCircle,
  Paperclip,
  Send,
} from "lucide-react";

import { AutopilotToggle } from "@/components/messages/autopilot-toggle";
import { AutoRefresh } from "@/components/messages/auto-refresh";
import { MarkReadOnView } from "@/components/messages/mark-read-button";
import { MessageComposer } from "@/components/messages/message-composer";
import { EmptyState } from "@/components/shared/empty-state";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { getConversation, listConversations } from "@/lib/api/conversations";
import type { Conversation, ConversationMessage } from "@/lib/api/types";
import { getLocale, getTranslations } from "@/i18n/server";
import { LOCALE_META, type Locale } from "@/i18n/config";
import type { TranslateFn } from "@/i18n/translate";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Messages" };

const CHANNEL_ICON = {
  email: Mail,
  sms: MessageCircle,
  whatsapp: MessageCircle,
  website: Globe,
  telegram: Send,
  instagram: MessageCircle,
  facebook: MessageCircle,
  google: Globe,
  youtube: MessageCircle,
} as const;

// The channels offered as inbox filters, in display order. Labels are proper
// nouns and stay untranslated; only the "all" reset is localised.
const FILTER_CHANNELS = [
  ["youtube", "YouTube"],
  ["whatsapp", "WhatsApp"],
  ["telegram", "Telegram"],
  ["instagram", "Instagram"],
  ["email", "Email"],
  ["website", "Website"],
] as const;

/**
 * The link a YouTube thread replies through. YouTube gives no back-channel, so
 * the "reply" is a jump to the public comment — carried on the newest message's
 * media (Radar posts it there). Newest first: the freshest comment wins.
 */
function youtubeReplyUrl(messages: ConversationMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const hit = messages[i].media?.find(
      (m) => m.url.includes("youtube.com") || m.url.includes("youtu.be"),
    );
    if (hit) return hit.url;
  }
  return null;
}

/**
 * The shared inbox, on live data since Phase 3.4.
 *
 * Selection is a search param rather than client state, so the thread is
 * server-rendered, linkable and back-button-correct — the same choice every
 * other detail view in the app makes.
 */
export default async function MessagesPage({
  searchParams,
}: {
  searchParams: Promise<{ c?: string; ch?: string }>;
}) {
  const { c, ch } = await searchParams;
  const t = await getTranslations();
  const locale = await getLocale();
  // `ch` narrows the inbox to one channel (the backend already filters); an
  // unknown value simply returns nothing, so a stale link can't 500.
  const channel = ch && ch in CHANNEL_ICON ? ch : undefined;
  const { data: conversations } = await listConversations({
    channel,
    limit: 50,
  });

  // Fall back to the first thread rather than trusting the parameter: a stale
  // bookmark to a deleted or now-invisible conversation should open the inbox,
  // not a 404.
  const selectedId =
    c && conversations.some((row) => row.id === c) ? c : conversations[0]?.id;
  const detail = selectedId ? await getConversation(selectedId) : null;

  return (
    <div className="h-[calc(100svh-7rem)] min-h-[560px]">
      {/* Live inbox: soft-refresh the server view so replies land without a
          manual reload or switching threads. */}
      <AutoRefresh intervalMs={2000} />
      <Card className="flex h-full flex-row gap-0 overflow-hidden p-0">
        <aside className="hidden w-[320px] shrink-0 flex-col border-r md:flex">
          <div className="border-b p-3">
            <h1 className="text-sm font-medium">{t("body.messagesTitle")}</h1>
            <p className="text-[11px] text-muted-foreground">
              {conversations.length === 0
                ? t("body.messagesEmptyTitle")
                : t("body.conversationsCount", { n: conversations.length })}
            </p>
            <div className="mt-2 flex flex-wrap gap-1">
              <ChannelChip label={t("body.chanAll")} active={!channel} />
              {FILTER_CHANNELS.map(([value, label]) => (
                <ChannelChip
                  key={value}
                  channel={value}
                  label={label}
                  active={channel === value}
                />
              ))}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {conversations.map((conversation) => (
              <ConversationRow
                key={conversation.id}
                conversation={conversation}
                channel={channel}
                active={conversation.id === selectedId}
              />
            ))}
          </div>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col">
          {detail ? (
            <>
              <MarkReadOnView
                conversationId={detail.conversation.id}
                unreadCount={detail.conversation.unread_count}
              />
              <ThreadHeader conversation={detail.conversation} t={t} />
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
                {detail.messages.map((message) => (
                  <MessageBubble
                    key={message.id}
                    message={message}
                    locale={locale}
                    t={t}
                  />
                ))}
              </div>
              {detail.conversation.channel === "youtube" ? (
                <YouTubeReply
                  url={youtubeReplyUrl(detail.messages)}
                  label={t("body.ytReply")}
                  noLinkLabel={t("body.ytNoLink")}
                />
              ) : (
                <MessageComposer
                  toAddress={detail.conversation.external_id}
                  toName={detail.conversation.display_name}
                  subject={detail.conversation.subject}
                />
              )}
            </>
          ) : (
            <div className="grid flex-1 place-items-center p-8">
              <EmptyState
                icon={Inbox}
                title={t("body.messagesEmptyTitle")}
                description={t("body.messagesEmptyDesc")}
              />
            </div>
          )}
        </section>
      </Card>
    </div>
  );
}

/**
 * A counterparty is not a workspace member — they have no stored hue and no
 * initials — so the avatar is derived from their address. Deterministic, so the
 * same person is the same colour on every render and every machine.
 */
function ContactAvatar({ name, className }: { name: string; className?: string }) {
  const hue =
    Array.from(name).reduce((total, char) => total + char.charCodeAt(0), 0) % 360;
  const initials = name
    .replace(/[^A-Za-z ]/g, " ")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("") || "?";

  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center rounded-full text-[11px] font-medium",
        className,
      )}
      style={{
        backgroundColor: `oklch(0.92 0.055 ${hue})`,
        color: `oklch(0.42 0.13 ${hue})`,
      }}
      aria-hidden
    >
      {initials}
    </span>
  );
}

/** A channel filter pill in the inbox header. No `channel` means the reset. */
function ChannelChip({
  channel,
  label,
  active,
}: {
  channel?: string;
  label: string;
  active: boolean;
}) {
  return (
    <Link
      href={channel ? `/messages?ch=${channel}` : "/messages"}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "border-transparent bg-primary text-primary-foreground"
          : "text-muted-foreground hover:bg-muted/50",
      )}
    >
      {label}
    </Link>
  );
}

/**
 * A YouTube thread's reply control. There is no back-channel to a YouTube
 * commenter, so instead of a composer we send the worker to the public comment
 * to answer it there. When no link came through, we say so rather than showing
 * a dead button.
 */
function YouTubeReply({
  url,
  label,
  noLinkLabel,
}: {
  url: string | null;
  label: string;
  noLinkLabel: string;
}) {
  if (!url) {
    return (
      <div className="border-t p-3 text-center text-[11px] text-muted-foreground">
        {noLinkLabel}
      </div>
    );
  }
  return (
    <div className="border-t p-3">
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        {label}
        <ExternalLink className="size-3.5" />
      </a>
    </div>
  );
}

function ConversationRow({
  conversation,
  channel,
  active,
}: {
  conversation: Conversation;
  channel: string | undefined;
  active: boolean;
}) {
  const Icon = CHANNEL_ICON[conversation.channel] ?? Mail;
  const name = conversation.display_name ?? conversation.external_id;
  // Keep the active channel filter when opening a thread, so selecting one
  // doesn't drop the user back to the full inbox.
  const href = channel
    ? `/messages?c=${conversation.id}&ch=${channel}`
    : `/messages?c=${conversation.id}`;

  return (
    <Link
      href={href}
      className={cn(
        "flex gap-2.5 border-b p-3 transition-colors hover:bg-muted/50",
        active && "bg-accent/40",
      )}
    >
      <ContactAvatar name={name} className="size-8" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="min-w-0 flex-1 truncate text-sm font-medium">{name}</p>
          <Icon className="size-3 shrink-0 text-muted-foreground" />
        </div>
        <p className="truncate text-[11px] text-muted-foreground">
          {conversation.last_message_preview ?? conversation.external_id}
        </p>
      </div>
      {conversation.unread_count > 0 && (
        <span className="mt-1 grid size-4 shrink-0 place-items-center rounded-full bg-primary text-[9px] font-semibold text-primary-foreground">
          {conversation.unread_count}
        </span>
      )}
    </Link>
  );
}

function ThreadHeader({
  conversation,
  t,
}: {
  conversation: Conversation;
  t: TranslateFn;
}) {
  const name = conversation.display_name ?? conversation.external_id;
  return (
    <div className="flex items-center gap-3 border-b p-3">
      <ContactAvatar name={name} className="size-8" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate text-[11px] text-muted-foreground">
          {conversation.external_id}
        </p>
      </div>
      <AutopilotToggle
        conversationId={conversation.id}
        autopilot={conversation.autopilot}
      />
      {conversation.entity_type ? (
        <Badge variant="secondary">
          {t("body.msgFiledOn", { entity: conversation.entity_type })}
        </Badge>
      ) : (
        // Worth saying out loud: an unfiled thread is visible to everyone and
        // is waiting for somebody to claim it.
        <Badge variant="outline">{t("body.msgUnfiled")}</Badge>
      )}
    </div>
  );
}

function MessageBubble({
  message,
  locale,
  t,
}: {
  message: ConversationMessage;
  locale: Locale;
  t: TranslateFn;
}) {
  const outbound = message.direction === "outbound";
  // Seamless translation: show the message in the viewer's own language when a
  // translation exists, and keep the client's original one tap away. Falls back
  // to the original text when the message was never translated.
  const translated = message.translations?.[locale];
  const display = translated || message.body_text;
  const isTranslated = Boolean(
    translated && message.lang && message.lang !== locale &&
    translated !== message.body_text,
  );
  return (
    <div className={cn("flex", outbound ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-lg border p-3",
          outbound ? "bg-primary/5" : "bg-muted/40",
        )}
      >
        {message.subject && (
          <p className="mb-1 text-xs font-medium">{message.subject}</p>
        )}
        {/* Plain text only. `body_html` arrived from outside and the API does
            not sanitise it — rendering it here would be a stored-XSS hole with
            somebody's inbox as the delivery mechanism. */}
        {display && <p className="text-sm whitespace-pre-wrap">{display}</p>}
        {message.media?.length > 0 && (
          <div className="mt-2 flex flex-col gap-2">
            {message.media.map((item, i) =>
              item.kind === "image" ? (
                <a
                  key={i}
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {/* Channel media is external; a plain img keeps it simple and
                      the app's own CSP governs what loads. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={item.url}
                    alt={item.name ?? ""}
                    className="max-h-52 w-auto max-w-full rounded-md border"
                  />
                </a>
              ) : (
                <a
                  key={i}
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  <Paperclip className="size-3.5 shrink-0" />
                  <span className="truncate">
                    {item.name ?? t("body.msgFile")}
                  </span>
                </a>
              ),
            )}
          </div>
        )}
        {isTranslated && (
          <details className="mt-1.5 group">
            <summary className="cursor-pointer list-none text-[10px] text-muted-foreground/80 hover:text-foreground">
              {t("body.msgTranslatedFrom", { lang: (message.lang ?? "").toUpperCase() })}
              {" · "}
              {t("body.msgShowOriginal")}
            </summary>
            <p className="mt-1 border-l-2 pl-2 text-sm whitespace-pre-wrap text-muted-foreground">
              {message.body_text}
            </p>
          </details>
        )}
        <p className="mt-1.5 text-[10px] text-muted-foreground">
          {message.status === "failed"
            ? (message.failure_reason ?? t("body.msgDeliveryFailed"))
            : message.status === "queued"
              ? t("body.msgSending")
              : new Date(message.sent_at ?? message.created_at).toLocaleString(
                  LOCALE_META[locale].htmlLang,
                )}
        </p>
      </div>
    </div>
  );
}
