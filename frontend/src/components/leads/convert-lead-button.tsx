"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2, TriangleAlert, UserCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ClientApiError } from "@/lib/api/client";
import { convertLead } from "@/lib/api/clients-client";
import type { ClientType } from "@/lib/api/types";

const TYPES = [
  "buyer",
  "seller",
  "investor",
  "landlord",
  "tenant",
  "other",
] as const;

function label(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Convert a lead into a client.
 *
 * Only the fields the lead cannot supply are asked for. Everything else —
 * name, contact details, tags, notes, owner — is carried over server-side, so
 * this dialog stays short and there is nothing to retype.
 *
 * `company_name` is offered because a lead is always a person by schema, while
 * the client they become may be the entity they act for.
 */
export function ConvertLeadButton({
  leadId,
  leadName,
}: {
  leadId: string;
  leadName: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [type, setType] = useState<ClientType>("buyer");
  const [companyName, setCompanyName] = useState("");

  async function handleConvert() {
    setPending(true);
    setError(null);
    try {
      const client = await convertLead(leadId, {
        type,
        company_name: companyName.trim() || null,
      });
      setOpen(false);
      router.push(`/clients/${client.id}`);
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Unable to convert. Please try again.",
      );
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button>
            <UserCheck className="size-4" />
            Convert to client
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Convert {leadName} to a client?</DialogTitle>
          <DialogDescription>
            Contact details, tags, notes and the current owner carry over. The
            lead stays in your pipeline, marked as converted.
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="convert-type">Client type</Label>
            <Select
              value={type}
              onValueChange={(next) => setType((next ?? "buyer") as ClientType)}
              disabled={pending}
            >
              <SelectTrigger id="convert-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TYPES.map((option) => (
                  <SelectItem key={option} value={option}>
                    {label(option)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="convert-company">Company name</Label>
            <Input
              id="convert-company"
              placeholder="Optional — for an LLC, trust or company"
              value={companyName}
              onChange={(event) => setCompanyName(event.target.value)}
              disabled={pending}
            />
          </div>
        </div>

        <DialogFooter>
          <DialogClose
            render={<Button variant="ghost" disabled={pending}>Cancel</Button>}
          />
          <Button onClick={handleConvert} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            Convert
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
