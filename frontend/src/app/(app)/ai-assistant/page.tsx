import type { Metadata } from "next";
import {
  ArrowUp,
  BarChart3,
  Building2,
  FileText,
  History,
  Mail,
  Paperclip,
  Plus,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Copy,
  Target,
} from "lucide-react";

import { BrandMark } from "@/components/shared/brand";
import { PageHeader } from "@/components/shared/page-header";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { currentUser } from "@/lib/mock-data";

export const metadata: Metadata = { title: "AI Assistant" };

const capabilities = [
  {
    icon: Target,
    title: "Prioritise my day",
    prompt: "Which five leads should I call first this morning, and why?",
  },
  {
    icon: BarChart3,
    title: "Explain the pipeline",
    prompt: "Why did weighted forecast drop 8% since last Monday?",
  },
  {
    icon: Mail,
    title: "Draft outreach",
    prompt: "Write a follow-up to Harper Lindqvist after her second showing.",
  },
  {
    icon: Building2,
    title: "Price a listing",
    prompt: "What should we list 2201 Folsom at, based on recent comps?",
  },
];

const recentThreads = [
  "Comps for 1428 Sanchez Street",
  "Q3 commission forecast by agent",
  "Why is Devon Pritchard unqualified?",
  "Draft open house invite — Sanchez",
  "Summarise the Haddad inspection report",
];

/** A single pre-baked exchange that demonstrates the response format. */
const sources = [
  { label: "12 leads", icon: Target },
  { label: "3 deals", icon: FileText },
  { label: "Activity log", icon: History },
];

export default function AiAssistantPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Assistant"
        description="Ask questions about your pipeline in plain language."
        actions={
          <>
            <Button variant="outline">
              <History className="size-4" />
              History
            </Button>
            <Button>
              <Plus className="size-4" />
              New chat
            </Button>
          </>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_300px]">
        {/* -------------------------------------------------------- chat */}
        <Card className="flex min-h-[620px] flex-col gap-0 p-0">
          <div className="scrollbar-slim flex-1 space-y-6 overflow-y-auto p-5">
            {/* user turn */}
            <div className="flex justify-end gap-3">
              <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">
                Which five leads should I call first this morning, and why?
              </div>
              <UserAvatar user={currentUser} size="sm" className="mt-auto" />
            </div>

            {/* assistant turn */}
            <div className="flex gap-3">
              <BrandMark className="mt-0.5 size-8 shrink-0" />
              <div className="min-w-0 flex-1 space-y-4">
                <div className="space-y-3 text-sm leading-relaxed">
                  <p>
                    Here&apos;s your call list, ordered by expected value rather
                    than raw score — two of these are time-sensitive.
                  </p>

                  <ol className="space-y-2.5">
                    {[
                      {
                        name: "Yuki Tanaka",
                        note: "Score 95. Reviewing the Folsom six-plex and asked about cap rates yesterday. Repeat investor — closed three deals with you since 2022.",
                      },
                      {
                        name: "Harper Lindqvist",
                        note: "Score 92, down 8 points. Toured 1428 Sanchez twice but hasn't heard from you in four days. Highest risk of going cold this week.",
                      },
                      {
                        name: "Theo Bergström",
                        note: "Score 88. Cash buyer, wants to move the Tuesday showing to 6pm — needs a yes or no today.",
                      },
                      {
                        name: "Amara Osei",
                        note: "Score 84. Pre-approved and flagged urgent. Under contract on Alameda at 78% probability.",
                      },
                      {
                        name: "Ines Moreau",
                        note: "Score 76. Six days without contact and financing is unresolved — the longest gap in your qualified set.",
                      },
                    ].map((item, index) => (
                      <li key={item.name} className="flex gap-3">
                        <span className="tabular grid size-5 shrink-0 place-items-center rounded-full bg-accent text-[11px] font-semibold text-accent-foreground">
                          {index + 1}
                        </span>
                        <p className="min-w-0">
                          <span className="font-medium">{item.name}</span>
                          {" — "}
                          <span className="text-muted-foreground">
                            {item.note}
                          </span>
                        </p>
                      </li>
                    ))}
                  </ol>

                  <p className="text-muted-foreground">
                    Want me to draft the opening message for any of these?
                  </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted-foreground">Sources:</span>
                  {sources.map((source) => (
                    <span
                      key={source.label}
                      className="flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] text-muted-foreground"
                    >
                      <source.icon className="size-3" />
                      {source.label}
                    </span>
                  ))}
                </div>

                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="icon-sm" aria-label="Copy response">
                    <Copy className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" aria-label="Good response">
                    <ThumbsUp className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" aria-label="Bad response">
                    <ThumbsDown className="size-4" />
                  </Button>
                </div>
              </div>
            </div>
          </div>

          {/* composer */}
          <div className="border-t p-4">
            <div className="rounded-xl border focus-within:ring-2 focus-within:ring-ring/40">
              <Textarea
                rows={2}
                placeholder="Ask about leads, deals, listings or performance…"
                className="resize-none border-0 shadow-none focus-visible:ring-0"
              />
              <div className="flex items-center gap-1 border-t px-2 py-1.5">
                <Button variant="ghost" size="icon-sm" aria-label="Attach context">
                  <Paperclip className="size-4" />
                </Button>
                <Separator orientation="vertical" className="mx-1 h-4" />
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Sparkles className="size-3" />
                  Scoped to your workspace
                </span>
                <Button size="icon-sm" className="ml-auto" aria-label="Send">
                  <ArrowUp className="size-4" />
                </Button>
              </div>
            </div>
            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              Responses in this prototype are static sample copy.
            </p>
          </div>
        </Card>

        {/* ------------------------------------------------------ sidebar */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Try asking</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {capabilities.map((item) => (
                <button
                  key={item.title}
                  className="flex w-full gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-muted/50"
                >
                  <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
                    <item.icon className="size-3.5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">
                      {item.title}
                    </span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                      {item.prompt}
                    </span>
                  </span>
                </button>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Recent</CardTitle>
            </CardHeader>
            <CardContent className="space-y-0.5">
              {recentThreads.map((thread) => (
                <button
                  key={thread}
                  className="block w-full truncate rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {thread}
                </button>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
