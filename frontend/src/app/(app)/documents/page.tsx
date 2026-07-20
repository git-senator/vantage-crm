import type { Metadata } from "next";
import {
  Clock,
  Download,
  FileCheck2,
  FileText,
  FolderPlus,
  HardDrive,
  Link2,
  MoreHorizontal,
  Upload,
  UploadCloud,
} from "lucide-react";

import { DataToolbar } from "@/components/shared/data-toolbar";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { titleize } from "@/lib/format";
import { documents } from "@/lib/mock-data";
import type { DocumentKind } from "@/types";

export const metadata: Metadata = { title: "Documents" };

const stats: Stat[] = [
  { label: "Total documents", value: "312", delta: 8.4, hint: "across 41 transactions", icon: FileText },
  { label: "Awaiting signature", value: "7", hint: "2 due this week", icon: FileCheck2 },
  { label: "Expiring soon", value: "3", hint: "within 14 days", icon: Clock },
  { label: "Storage used", value: "18.4 GB", hint: "of 100 GB", icon: HardDrive },
];

const folders: { name: string; kind: DocumentKind; count: number }[] = [
  { name: "Contracts", kind: "contract", count: 84 },
  { name: "Disclosures", kind: "disclosure", count: 112 },
  { name: "Inspections", kind: "inspection", count: 46 },
  { name: "Listings", kind: "listing", count: 39 },
  { name: "Financial", kind: "financial", count: 31 },
];

const kindTone: Record<DocumentKind, string> = {
  contract: "268",
  disclosure: "200",
  inspection: "152",
  listing: "30",
  financial: "340",
};

export default function DocumentsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Documents"
        description="Transaction files, disclosures and signature status in one library."
        actions={
          <>
            <Button variant="outline">
              <FolderPlus className="size-4" />
              New folder
            </Button>
            <Button>
              <Upload className="size-4" />
              Upload
            </Button>
          </>
        }
      />

      <StatGrid stats={stats} />

      <div className="grid gap-6 lg:grid-cols-[240px_1fr]">
        {/* ------------------------------------------------------ folders */}
        <div className="space-y-4">
          <Card className="gap-0 py-0">
            <CardHeader className="border-b px-4 py-3">
              <CardTitle className="text-sm">Folders</CardTitle>
            </CardHeader>
            <CardContent className="space-y-0.5 p-2">
              {folders.map((folder) => (
                <button
                  key={folder.name}
                  className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-sm transition-colors hover:bg-muted"
                >
                  <span
                    className="grid size-7 shrink-0 place-items-center rounded-md"
                    style={{
                      backgroundColor: `oklch(0.93 0.05 ${kindTone[folder.kind]})`,
                      color: `oklch(0.45 0.13 ${kindTone[folder.kind]})`,
                    }}
                  >
                    <FileText className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1 truncate">{folder.name}</span>
                  <span className="tabular text-xs text-muted-foreground">
                    {folder.count}
                  </span>
                </button>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Storage</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Progress value={18.4} className="h-2" />
              <p className="text-xs text-muted-foreground">
                18.4 GB of 100 GB used
              </p>
              <Button variant="outline" size="sm" className="mt-1 w-full">
                Manage plan
              </Button>
            </CardContent>
          </Card>

          {/* Upload affordance — visual only. */}
          <div className="flex flex-col items-center rounded-xl border border-dashed p-5 text-center">
            <UploadCloud className="size-6 text-muted-foreground" />
            <p className="mt-2.5 text-sm font-medium">Drop files here</p>
            <p className="mt-1 text-xs text-muted-foreground">
              PDF, DOCX and images up to 50 MB
            </p>
            <Button variant="outline" size="sm" className="mt-3">
              Browse files
            </Button>
          </div>
        </div>

        {/* -------------------------------------------------------- table */}
        <div className="min-w-0 space-y-4">
          <Tabs defaultValue="all">
            <TabsList>
              <TabsTrigger value="all">All files</TabsTrigger>
              <TabsTrigger value="recent">Recent</TabsTrigger>
              <TabsTrigger value="shared">Shared with me</TabsTrigger>
              <TabsTrigger value="signature">Needs signature</TabsTrigger>
            </TabsList>
          </Tabs>

          <DataToolbar placeholder="Search documents…" />

          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="w-10 pl-4">
                      <Checkbox aria-label="Select all documents" />
                    </TableHead>
                    <TableHead className="min-w-[280px]">Name</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Related to</TableHead>
                    <TableHead>Owner</TableHead>
                    <TableHead>Size</TableHead>
                    <TableHead>Modified</TableHead>
                    <TableHead className="w-12" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {documents.map((doc) => (
                    <TableRow key={doc.id} className="cursor-pointer">
                      <TableCell className="pl-4">
                        <Checkbox aria-label={`Select ${doc.name}`} />
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2.5">
                          <span
                            className="grid size-8 shrink-0 place-items-center rounded-lg"
                            style={{
                              backgroundColor: `oklch(0.93 0.05 ${kindTone[doc.kind]})`,
                              color: `oklch(0.45 0.13 ${kindTone[doc.kind]})`,
                            }}
                          >
                            <FileText className="size-4" />
                          </span>
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">
                              {doc.name}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {titleize(doc.kind)}
                            </p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={doc.status} />
                      </TableCell>
                      <TableCell>
                        <span className="flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                          <Link2 className="size-3" />
                          {doc.relatedTo}
                        </span>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <UserAvatar user={doc.owner} size="xs" />
                          <span className="hidden text-sm xl:inline">
                            {doc.owner.name.split(" ")[0]}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="tabular whitespace-nowrap text-muted-foreground">
                        {doc.size}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {doc.updatedAt}
                      </TableCell>
                      <TableCell>
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            render={
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                aria-label={`Actions for ${doc.name}`}
                              >
                                <MoreHorizontal className="size-4" />
                              </Button>
                            }
                          />
                          <DropdownMenuContent align="end" className="w-44">
                            <DropdownMenuItem>Preview</DropdownMenuItem>
                            <DropdownMenuItem>
                              <Download className="size-4" />
                              Download
                            </DropdownMenuItem>
                            <DropdownMenuItem>Request signature</DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive">
                              Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
