import type { Metadata } from "next";
import {
  Building2,
  Grid2x2,
  Mail,
  MoreHorizontal,
  Phone,
  Plus,
  Repeat,
  Users,
  Wallet,
} from "lucide-react";

import { DataToolbar } from "@/components/shared/data-toolbar";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatPrice, titleize } from "@/lib/format";
import { clients } from "@/lib/mock-data";
import type { TeamMember } from "@/types";

export const metadata: Metadata = { title: "Clients" };

const stats: Stat[] = [
  { label: "Active clients", value: "127", delta: 6.8, hint: "vs. last quarter", icon: Users },
  { label: "Lifetime volume", value: "$48.2M", delta: 14.1, hint: "all time", icon: Wallet },
  { label: "Properties managed", value: "36", delta: 3.2, hint: "under representation", icon: Building2 },
  { label: "Repeat client rate", value: "31%", delta: 4.6, hint: "trailing 12 months", icon: Repeat },
];

/** Clients aren't team members, but the avatar takes the same shape. */
function asAvatarSubject(name: string, hue: number): TeamMember {
  const initials = name
    .replace(/&/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  return { id: name, name, initials, role: "Client", hue };
}

const hues = [268, 200, 152, 30, 340, 78, 12];

export default function ClientsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Clients"
        description="Everyone you actively represent, plus the relationships worth reviving."
        actions={
          <>
            <Button variant="outline">
              <Grid2x2 className="size-4" />
              Board view
            </Button>
            <Button>
              <Plus className="size-4" />
              Add client
            </Button>
          </>
        }
      />

      <StatGrid stats={stats} />

      <Tabs defaultValue="all">
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="buyers">Buyers</TabsTrigger>
          <TabsTrigger value="sellers">Sellers</TabsTrigger>
          <TabsTrigger value="investors">Investors</TabsTrigger>
          <TabsTrigger value="dormant">Dormant</TabsTrigger>
        </TabsList>
      </Tabs>

      <DataToolbar placeholder="Search clients by name, email or market…" />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {clients.map((client, index) => {
          const subject = asAvatarSubject(client.name, hues[index % hues.length]);
          return (
            <Card key={client.id} className="gap-0 py-0 transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-start gap-3">
                  <UserAvatar user={subject} size="lg" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{client.name}</p>
                    <p className="truncate text-sm text-muted-foreground">
                      {client.location}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <StatusBadge status={client.status} />
                      <StatusBadge
                        status={client.type}
                        label={titleize(client.type)}
                        tone="neutral"
                        dot={false}
                      />
                    </div>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Actions for ${client.name}`}
                        >
                          <MoreHorizontal className="size-4" />
                        </Button>
                      }
                    />
                    <DropdownMenuContent align="end" className="w-44">
                      <DropdownMenuItem>Open record</DropdownMenuItem>
                      <DropdownMenuItem>Log activity</DropdownMenuItem>
                      <DropdownMenuItem>Add to campaign</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem variant="destructive">
                        Archive
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>

                <dl className="mt-5 grid grid-cols-3 gap-2 rounded-lg bg-muted/50 p-3 text-center">
                  <div>
                    <dt className="text-[11px] text-muted-foreground">
                      Lifetime
                    </dt>
                    <dd className="tabular mt-0.5 text-sm font-semibold">
                      {client.lifetimeValue > 0
                        ? formatPrice(client.lifetimeValue)
                        : "—"}
                    </dd>
                  </div>
                  <div className="border-x">
                    <dt className="text-[11px] text-muted-foreground">
                      Properties
                    </dt>
                    <dd className="tabular mt-0.5 text-sm font-semibold">
                      {client.properties}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[11px] text-muted-foreground">Since</dt>
                    <dd className="mt-0.5 text-sm font-semibold">
                      {client.since}
                    </dd>
                  </div>
                </dl>
              </CardContent>

              <Separator />

              <CardFooter className="flex items-center justify-between gap-2 px-5 py-3">
                <div className="flex min-w-0 items-center gap-2">
                  <UserAvatar user={client.owner} size="xs" />
                  <span className="truncate text-xs text-muted-foreground">
                    {client.owner.name}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button variant="ghost" size="icon-sm" aria-label="Call">
                    <Phone className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" aria-label="Email">
                    <Mail className="size-4" />
                  </Button>
                </div>
              </CardFooter>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
