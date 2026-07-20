import type { Metadata } from "next";
import {
  Mail,
  MessageCircle,
  Paperclip,
  Phone,
  Pin,
  Search,
  Send,
  Smile,
  Sparkles,
  Video,
} from "lucide-react";

import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { contacts, conversations, messageThread } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Messages" };

const channelIcons = {
  sms: MessageCircle,
  email: Mail,
  whatsapp: MessageCircle,
} as const;

const suggestedReplies = [
  "Saturday at 10am works — I'll confirm with the listing agent.",
  "Sending the comps within the hour.",
  "Would 11am suit you better?",
];

export default function MessagesPage() {
  const active = conversations[0];

  return (
    // Fills the viewport minus the topbar so the thread scrolls, not the page.
    <div className="h-[calc(100svh-7rem)] min-h-[560px]">
      <Card className="flex h-full flex-row gap-0 overflow-hidden p-0">
        {/* ------------------------------------------------ conversations */}
        <aside className="hidden w-[300px] shrink-0 flex-col border-r md:flex">
          <div className="space-y-3 border-b p-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Inbox</h2>
              <StatusBadge status="unread" label="3 unread" tone="brand" dot={false} />
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input placeholder="Search messages…" className="pl-9" />
            </div>
            <Tabs defaultValue="all">
              <TabsList className="w-full">
                <TabsTrigger value="all" className="flex-1">
                  All
                </TabsTrigger>
                <TabsTrigger value="unread" className="flex-1">
                  Unread
                </TabsTrigger>
                <TabsTrigger value="sms" className="flex-1">
                  SMS
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          <div className="scrollbar-slim flex-1 overflow-y-auto">
            {conversations.map((conversation) => {
              const ChannelIcon = channelIcons[conversation.channel];
              const isActive = conversation.id === active.id;

              return (
                <button
                  key={conversation.id}
                  className={cn(
                    "flex w-full gap-3 border-b p-3 text-left transition-colors hover:bg-muted/50",
                    isActive && "bg-accent/60",
                  )}
                >
                  <UserAvatar user={conversation.contact} size="md" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-medium">
                        {conversation.contact.name}
                      </span>
                      {conversation.pinned && (
                        <Pin className="size-3 shrink-0 text-muted-foreground" />
                      )}
                      <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
                        {conversation.timestamp}
                      </span>
                    </div>
                    <p
                      className={cn(
                        "mt-0.5 line-clamp-2 text-xs leading-relaxed",
                        conversation.unread > 0
                          ? "font-medium text-foreground"
                          : "text-muted-foreground",
                      )}
                    >
                      {conversation.preview}
                    </p>
                    <div className="mt-1.5 flex items-center gap-2">
                      <ChannelIcon className="size-3 text-muted-foreground" />
                      <span className="text-[11px] text-muted-foreground capitalize">
                        {conversation.channel}
                      </span>
                      {conversation.unread > 0 && (
                        <span className="tabular ml-auto grid size-4 place-items-center rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">
                          {conversation.unread}
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        {/* ------------------------------------------------------ thread */}
        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b p-3">
            <UserAvatar user={active.contact} size="md" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">
                {active.contact.name}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                Buyer · Noe Valley · lead score 92
              </p>
            </div>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon-sm" aria-label="Call">
                <Phone className="size-4" />
              </Button>
              <Button variant="ghost" size="icon-sm" aria-label="Video call">
                <Video className="size-4" />
              </Button>
            </div>
          </header>

          <div className="scrollbar-slim flex-1 space-y-4 overflow-y-auto p-4">
            <div className="flex justify-center">
              <span className="rounded-full bg-muted px-2.5 py-1 text-[11px] text-muted-foreground">
                Today
              </span>
            </div>

            {messageThread.map((message) => {
              const mine = message.author === "me";
              return (
                <div
                  key={message.id}
                  className={cn("flex gap-2.5", mine && "flex-row-reverse")}
                >
                  {!mine && <UserAvatar user={contacts.harper} size="xs" className="mt-auto" />}
                  <div
                    className={cn(
                      "max-w-[75%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed sm:max-w-[65%]",
                      mine
                        ? "rounded-br-md bg-primary text-primary-foreground"
                        : "rounded-bl-md bg-muted",
                    )}
                  >
                    <p>{message.body}</p>
                    <p
                      className={cn(
                        "mt-1 text-[10px]",
                        mine
                          ? "text-primary-foreground/60"
                          : "text-muted-foreground",
                      )}
                    >
                      {message.timestamp}
                      {message.status && ` · ${message.status}`}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="border-t p-3">
            <div className="mb-2.5 flex flex-wrap items-center gap-2">
              <span className="flex items-center gap-1 text-[11px] font-medium text-primary">
                <Sparkles className="size-3" />
                Suggested
              </span>
              {suggestedReplies.map((reply) => (
                <button
                  key={reply}
                  className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {reply}
                </button>
              ))}
            </div>

            <div className="rounded-xl border focus-within:ring-2 focus-within:ring-ring/40">
              <Textarea
                placeholder="Write a message…"
                rows={2}
                className="resize-none border-0 shadow-none focus-visible:ring-0"
              />
              <div className="flex items-center gap-1 border-t px-2 py-1.5">
                <Button variant="ghost" size="icon-sm" aria-label="Attach a file">
                  <Paperclip className="size-4" />
                </Button>
                <Button variant="ghost" size="icon-sm" aria-label="Add emoji">
                  <Smile className="size-4" />
                </Button>
                <Separator orientation="vertical" className="mx-1 h-4" />
                <span className="text-[11px] text-muted-foreground">
                  Sending as SMS
                </span>
                <Button size="sm" className="ml-auto">
                  <Send className="size-3.5" />
                  Send
                </Button>
              </div>
            </div>
          </div>
        </section>
      </Card>
    </div>
  );
}
