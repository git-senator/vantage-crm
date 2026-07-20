import type { Metadata } from "next";
import {
  Download,
  Flame,
  MoreHorizontal,
  Plus,
  Sparkles,
  Target,
  UserPlus,
} from "lucide-react";

import { DataToolbar } from "@/components/shared/data-toolbar";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatRange } from "@/lib/format";
import { leads } from "@/lib/mock-data";

export const metadata: Metadata = { title: "Leads" };

const stats: Stat[] = [
  { label: "Total leads", value: "508", delta: 18.2, hint: "this quarter", icon: Target },
  { label: "Hot leads", value: "42", delta: 9.4, hint: "score ≥ 80", icon: Flame },
  { label: "Avg. response time", value: "11m", delta: -34.5, hint: "vs. 4h in Q1", invertDelta: true, icon: Sparkles },
  { label: "Lead → client rate", value: "5.3%", delta: 1.1, hint: "trailing 90 days", icon: UserPlus },
];

const tabs = [
  { value: "all", label: "All leads", count: 8 },
  { value: "mine", label: "Assigned to me", count: 2 },
  { value: "unassigned", label: "Unassigned", count: 0 },
  { value: "hot", label: "Hot", count: 4 },
];

/** Score pill colour tracks the same thresholds the AI copy references. */
function scoreTone(score: number) {
  if (score >= 80) return "bg-success/12 text-success";
  if (score >= 60) return "bg-warning/18 text-warning-foreground dark:bg-warning/20 dark:text-warning";
  return "bg-muted text-muted-foreground";
}

export default function LeadsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Leads"
        description="Inbound and sourced prospects, scored and routed to an owner."
        actions={
          <>
            <Button variant="outline">
              <Download className="size-4" />
              Export
            </Button>
            <Button>
              <Plus className="size-4" />
              Add lead
            </Button>
          </>
        }
      />

      <StatGrid stats={stats} />

      <Tabs defaultValue="all">
        <TabsList>
          {tabs.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value} className="gap-1.5">
              {tab.label}
              <span className="tabular rounded-full bg-muted px-1.5 text-[11px] text-muted-foreground">
                {tab.count}
              </span>
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <DataToolbar
        placeholder="Search leads by name, email or location…"
        filters={
          <>
            <Select defaultValue="All stages">
              <SelectTrigger size="sm" className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="All stages">All stages</SelectItem>
                <SelectItem value="New">New</SelectItem>
                <SelectItem value="Contacted">Contacted</SelectItem>
                <SelectItem value="Qualified">Qualified</SelectItem>
                <SelectItem value="Touring">Touring</SelectItem>
              </SelectContent>
            </Select>
            <Select defaultValue="All sources">
              <SelectTrigger size="sm" className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="All sources">All sources</SelectItem>
                <SelectItem value="Zillow">Zillow</SelectItem>
                <SelectItem value="Referral">Referral</SelectItem>
                <SelectItem value="Website">Website</SelectItem>
              </SelectContent>
            </Select>
          </>
        }
      />

      <Card className="overflow-hidden p-0">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-10 pl-4">
                  <Checkbox aria-label="Select all leads" />
                </TableHead>
                <TableHead className="min-w-[200px]">Lead</TableHead>
                <TableHead className="w-20">Score</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead>Source</TableHead>
                <TableHead className="min-w-[160px]">Budget</TableHead>
                <TableHead className="min-w-[150px]">Location</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead>Last touch</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {leads.map((lead) => (
                <TableRow key={lead.id} className="cursor-pointer">
                  <TableCell className="pl-4">
                    <Checkbox aria-label={`Select ${lead.name}`} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium">{lead.name}</span>
                      {lead.temperature === "hot" && (
                        <Flame className="size-3.5 text-destructive" />
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">{lead.email}</p>
                  </TableCell>
                  <TableCell>
                    <span
                      className={`tabular inline-grid size-8 place-items-center rounded-lg text-xs font-semibold ${scoreTone(lead.score)}`}
                    >
                      {lead.score}
                    </span>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={lead.stage} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {lead.source}
                  </TableCell>
                  <TableCell className="tabular whitespace-nowrap">
                    {formatRange(lead.budget)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {lead.location}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <UserAvatar user={lead.owner} size="xs" />
                      <span className="hidden text-sm xl:inline">
                        {lead.owner.name.split(" ")[0]}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {lead.lastTouch}
                  </TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        render={
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Actions for ${lead.name}`}
                          >
                            <MoreHorizontal className="size-4" />
                          </Button>
                        }
                      />
                      <DropdownMenuContent align="end" className="w-44">
                        <DropdownMenuItem>View details</DropdownMenuItem>
                        <DropdownMenuItem>Log a call</DropdownMenuItem>
                        <DropdownMenuItem>Send email</DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem>Convert to client</DropdownMenuItem>
                        <DropdownMenuItem variant="destructive">
                          Mark unqualified
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <div className="flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">
            Showing <span className="font-medium text-foreground">8</span> of{" "}
            <span className="font-medium text-foreground">508</span> leads
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled>
              Previous
            </Button>
            <Button variant="outline" size="sm">
              Next
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
