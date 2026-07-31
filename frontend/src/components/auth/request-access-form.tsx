"use client";

import { useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle2, Loader2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "@/i18n/language-provider";
import { submitAccessRequest } from "@/lib/api/access-client";
import { ClientApiError } from "@/lib/api/client";

/**
 * Public "request access" form.
 *
 * The API answers the same way whether or not the email already has an account,
 * so this shows one success state and never reveals membership.
 */
export function RequestAccessForm() {
  const { t } = useTranslation();

  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    const form = new FormData(event.currentTarget);
    try {
      await submitAccessRequest({
        full_name: String(form.get("full_name") ?? "").trim(),
        email: String(form.get("email") ?? "").trim(),
        requested_role: String(form.get("requested_role") ?? "").trim() || null,
        message: String(form.get("message") ?? "").trim() || null,
      });
      setDone(true);
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : t("access.requestError"),
      );
      setPending(false);
    }
  }

  if (done) {
    return (
      <div
        role="status"
        className="mt-8 flex items-start gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/8 p-4 text-sm"
      >
        <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-emerald-600" />
        <div className="space-y-1">
          <p className="font-medium text-foreground">
            {t("access.submittedTitle")}
          </p>
          <p className="text-muted-foreground">{t("access.submittedBody")}</p>
        </div>
      </div>
    );
  }

  return (
    <form className="mt-8 space-y-4" onSubmit={handleSubmit} noValidate>
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="full_name">{t("access.fullName")}</Label>
        <Input
          id="full_name"
          name="full_name"
          required
          autoComplete="name"
          disabled={pending}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="email">{t("access.emailLabel")}</Label>
        <Input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          placeholder="you@company.com"
          disabled={pending}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="requested_role">{t("access.roleLabel")}</Label>
        <Input
          id="requested_role"
          name="requested_role"
          placeholder={t("access.rolePlaceholder")}
          disabled={pending}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="message">{t("access.messageLabel")}</Label>
        <Textarea
          id="message"
          name="message"
          rows={3}
          placeholder={t("access.messagePlaceholder")}
          disabled={pending}
        />
      </div>

      <Button size="lg" className="w-full" type="submit" disabled={pending}>
        {pending ? (
          <>
            <Loader2 className="size-4 animate-spin" />
            {t("access.submitting")}
          </>
        ) : (
          <>
            {t("access.submit")}
            <ArrowRight className="size-4" />
          </>
        )}
      </Button>
    </form>
  );
}
