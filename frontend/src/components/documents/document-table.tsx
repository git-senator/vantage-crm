"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  Download,
  FileText,
  Link2,
  Loader2,
  MoreHorizontal,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";

import { OwnerAvatar } from "@/components/shared/owner-avatar";
import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ClientApiError } from "@/lib/api/client";
import {
  deleteAttachment,
  downloadAttachment,
  formatBytes,
} from "@/lib/api/attachments-client";
import type { Attachment } from "@/lib/api/types";

const ENTITY_PATH: Record<string, string> = {
  lead: "/leads",
  client: "/clients",
  property: "/properties",
  deal: "/deals",
};

function DeleteDocumentDialog({ doc }: { doc: Attachment }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setPending(true);
    setError(null);
    try {
      await deleteAttachment(doc.id);
      setOpen(false);
      toast.success("Document deleted");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Unable to delete. Please try again.",
      );
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DropdownMenuItem
        variant="destructive"
        onSelect={(e) => {
          e.preventDefault();
          setOpen(true);
        }}
      >
        <Trash2 className="size-4" />
        Delete
      </DropdownMenuItem>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete this document?</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          “{doc.filename}” will be removed. This cannot be undone from here.
        </p>
        {error && (
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <DialogFooter>
          <DialogClose
            render={<Button variant="ghost" disabled={pending}>Cancel</Button>}
          />
          <Button variant="destructive" onClick={handleDelete} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            Delete document
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DocumentRow({ doc }: { doc: Attachment }) {
  const [downloading, setDownloading] = useState(false);
  const available = doc.status === "available";

  async function handleDownload() {
    setDownloading(true);
    try {
      await downloadAttachment(doc.id);
    } catch (caught) {
      toast.error(
        caught instanceof ClientApiError ? caught.message : "Download failed",
      );
    } finally {
      setDownloading(false);
    }
  }

  const entityPath = ENTITY_PATH[doc.entity_type];

  return (
    <TableRow>
      <TableCell className="pl-4">
        <div className="flex items-center gap-2.5">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <FileText className="size-4" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{doc.filename}</p>
            <p className="truncate text-xs text-muted-foreground">
              {doc.content_type}
            </p>
          </div>
        </div>
      </TableCell>
      <TableCell>
        <StatusBadge status={doc.status} />
      </TableCell>
      <TableCell>
        {entityPath ? (
          <Link
            href={`${entityPath}/${doc.entity_id}`}
            className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground capitalize transition-colors hover:text-foreground"
          >
            <Link2 className="size-3" />
            {doc.entity_type}
          </Link>
        ) : (
          <span className="text-xs text-muted-foreground capitalize">
            {doc.entity_type}
          </span>
        )}
      </TableCell>
      <TableCell>
        {doc.uploader ? (
          <div className="flex items-center gap-2">
            <OwnerAvatar owner={doc.uploader} size="xs" />
            <span className="hidden text-sm xl:inline">
              {doc.uploader.full_name.split(" ")[0]}
            </span>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="tabular whitespace-nowrap text-muted-foreground">
        {doc.size_bytes != null ? formatBytes(doc.size_bytes) : "—"}
      </TableCell>
      <TableCell
        className="whitespace-nowrap text-muted-foreground"
        suppressHydrationWarning
      >
        {new Date(doc.created_at).toLocaleDateString()}
      </TableCell>
      <TableCell>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Actions for ${doc.filename}`}
              >
                <MoreHorizontal className="size-4" />
              </Button>
            }
          />
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem
              disabled={!available || downloading}
              onSelect={(e) => {
                e.preventDefault();
                void handleDownload();
              }}
            >
              {downloading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Download className="size-4" />
              )}
              Download
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DeleteDocumentDialog doc={doc} />
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  );
}

export function DocumentTable({ documents }: { documents: Attachment[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="min-w-[280px] pl-4">Name</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Related to</TableHead>
            <TableHead>Uploaded by</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Added</TableHead>
            <TableHead className="w-12" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {documents.map((doc) => (
            <DocumentRow key={doc.id} doc={doc} />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
