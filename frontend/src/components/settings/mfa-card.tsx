"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2, ShieldAlert, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTranslation } from "@/i18n/language-provider";
import { ClientApiError } from "@/lib/api/client";
import {
  activateMfa,
  beginMfaEnrolment,
  disableMfa,
  regenerateRecoveryCodes,
} from "@/lib/api/mfa-client";
import type { MfaStatus } from "@/lib/api/types";

type Stage = "idle" | "enrolling" | "codes";

/**
 * Two-factor authentication.
 *
 * Enrolment is two steps because the API is: a secret is issued, and MFA turns
 * on only once a code from it verifies. Showing the secret and declaring
 * victory would leave people locked out of their own accounts with a secret
 * they never successfully scanned.
 *
 * The recovery codes are displayed once, right after activation, because they
 * are stored hashed — there is no endpoint that can show them again, and this
 * component is deliberate about saying so.
 */
export function MfaCard({ status }: { status: MfaStatus }) {
  const router = useRouter();
  const { t } = useTranslation();
  const [stage, setStage] = useState<Stage>("idle");
  const [secret, setSecret] = useState<string | null>(null);
  const [uri, setUri] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setPending(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError ? caught.message : t("body.errGeneric"),
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3 space-y-0">
        <CardTitle className="flex items-center gap-2">
          {status.enabled ? (
            <ShieldCheck className="size-4 text-success" />
          ) : (
            <ShieldAlert className="size-4 text-muted-foreground" />
          )}
          {t("body.mfaTitle")}
        </CardTitle>
        {status.enabled ? (
          <Badge>{t("body.mfaOn")}</Badge>
        ) : status.setup_required ? (
          // Worth stating plainly: their role obliges it, and the login still
          // works — this is a nudge with teeth, not a lockout.
          <Badge variant="destructive">{t("body.mfaRequired")}</Badge>
        ) : (
          <Badge variant="outline">{t("body.mfaOff")}</Badge>
        )}
      </CardHeader>

      <CardContent className="space-y-4">
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        {stage === "codes" && (
          <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/5 p-3">
            <p className="text-sm font-medium">{t("body.mfaSaveCodes")}</p>
            <p className="text-xs text-muted-foreground">
              {t("body.mfaCodesHint")}
            </p>
            <ul className="grid grid-cols-2 gap-1 font-mono text-xs">
              {codes.map((recoveryCode) => (
                <li key={recoveryCode}>{recoveryCode}</li>
              ))}
            </ul>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setStage("idle");
                setCodes([]);
                router.refresh();
              }}
            >
              {t("body.mfaSavedThem")}
            </Button>
          </div>
        )}

        {stage === "enrolling" && secret && (
          <div className="space-y-3">
            <div className="space-y-1">
              <p className="text-sm">{t("body.mfaAddToApp")}</p>
              <p className="font-mono text-xs break-all text-muted-foreground">
                {secret}
              </p>
              {uri && (
                <p className="text-[11px] break-all text-muted-foreground">
                  {uri}
                </p>
              )}
            </div>
            <div className="flex items-end gap-2">
              <div className="space-y-1">
                <Label htmlFor="mfa-code" className="text-xs">
                  {t("body.mfaCode")}
                </Label>
                <Input
                  id="mfa-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="000000"
                  className="w-32 font-mono"
                  disabled={pending}
                />
              </div>
              <Button
                size="sm"
                disabled={pending || code.trim().length < 6}
                onClick={() =>
                  run(async () => {
                    const result = await activateMfa(code.trim());
                    setCodes(result.recovery_codes);
                    setCode("");
                    setSecret(null);
                    setStage("codes");
                  })
                }
              >
                {pending && <Loader2 className="size-4 animate-spin" />}
                {t("body.mfaTurnOn")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={pending}
                onClick={() => {
                  setStage("idle");
                  setSecret(null);
                }}
              >
                {t("buttons.cancel")}
              </Button>
            </div>
          </div>
        )}

        {stage === "idle" && !status.enabled && (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {t("body.mfaProtect")}
            </p>
            <Button
              size="sm"
              disabled={pending}
              onClick={() =>
                run(async () => {
                  const started = await beginMfaEnrolment();
                  setSecret(started.secret);
                  setUri(started.provisioning_uri);
                  setStage("enrolling");
                })
              }
            >
              {pending && <Loader2 className="size-4 animate-spin" />}
              {t("body.mfaSetUp")}
            </Button>
          </div>
        )}

        {stage === "idle" && status.enabled && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {t("body.mfaCodesLeft", { n: status.recovery_codes_remaining })}
            </p>
            <div className="space-y-1">
              <Label htmlFor="mfa-password" className="text-xs">
                {t("body.mfaConfirmPw")}
              </Label>
              <Input
                id="mfa-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                disabled={pending}
                className="max-w-xs"
              />
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={pending || !password}
                onClick={() =>
                  run(async () => {
                    const result = await regenerateRecoveryCodes(password);
                    setPassword("");
                    setCodes(result.recovery_codes);
                    setStage("codes");
                  })
                }
              >
                {t("body.mfaNewCodes")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="text-destructive"
                disabled={pending || !password}
                onClick={() =>
                  run(async () => {
                    await disableMfa(password);
                    setPassword("");
                    router.refresh();
                  })
                }
              >
                {t("body.mfaTurnOff")}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
