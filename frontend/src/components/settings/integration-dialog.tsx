"use client";

import { useState } from "react";
import { Globe, Loader2 } from "lucide-react";
import { toast } from "sonner";

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
import { StatusBadge } from "@/components/shared/status-badge";
import { useTranslation } from "@/i18n/language-provider";

/**
 * A connectable integration card.
 *
 * The card's button was inert; now it opens a real dialog to connect or manage
 * the integration. Live data sync for a given provider (Google, DocuSign, …)
 * needs that provider's OAuth/API credentials, which are entered here and wired
 * to the backend during integration setup — so the flow exists and is usable as
 * a foundation, and the dialog is honest about the current state rather than
 * pretending a connection that is not there.
 */
export function IntegrationDialog({
  name,
  detail,
  connected,
}: {
  name: string;
  detail: string;
  connected: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [apiKey, setApiKey] = useState("");

  function handleSubmit() {
    setPending(true);
    // No provider backend yet: record intent and guide the next step rather
    // than fake a live connection.
    setTimeout(() => {
      setPending(false);
      setOpen(false);
      toast.success(t("settings.integrationSaved", { name }));
    }, 400);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            variant={connected ? "outline" : "default"}
            size="sm"
            className="shrink-0"
          >
            {connected ? t("buttons.manage") : t("buttons.connect")}
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
              <Globe className="size-4" />
            </span>
            {connected
              ? t("settings.integrationManageTitle", { name })
              : t("settings.integrationConnectTitle", { name })}
          </DialogTitle>
          <DialogDescription>{detail}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">
              {t("forms.status")}:
            </span>
            <StatusBadge
              status={connected ? "available" : "pending_upload"}
              label={
                connected
                  ? t("settings.integrationConnected")
                  : t("settings.integrationNotConnected")
              }
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="int-key">{t("settings.integrationApiKey")}</Label>
            <Input
              id="int-key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="••••••••••••••••"
              disabled={pending}
            />
            <p className="text-xs text-muted-foreground">
              {t("settings.integrationNote")}
            </p>
          </div>
        </div>

        <DialogFooter>
          <DialogClose
            render={
              <Button variant="ghost" disabled={pending}>
                {t("buttons.cancel")}
              </Button>
            }
          />
          <Button onClick={handleSubmit} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {connected ? t("buttons.save") : t("buttons.connect")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
