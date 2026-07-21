"use client";

import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ArrowRight, Loader2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ClientApiError, apiRequest } from "@/lib/api/client";
import type { SessionResponse } from "@/lib/api/types";

/**
 * Credential form.
 *
 * Markup and classes are unchanged from the approved prototype — only the
 * inert `<Link href="/dashboard">` became a real submit. Tokens arrive as
 * httpOnly cookies set by the API, so nothing here touches a token.
 */
export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    try {
      await apiRequest<SessionResponse>("/auth/login", {
        method: "POST",
        body: { email, password },
      });

      // Only a relative path is honoured. Accepting an absolute URL from the
      // query string would be an open redirect.
      const next = searchParams.get("next");
      const destination = next?.startsWith("/") && !next.startsWith("//")
        ? next
        : "/dashboard";

      // refresh() re-runs server components so the new session is picked up
      // before the navigation renders.
      router.replace(destination);
      router.refresh();
    } catch (caught) {
      if (caught instanceof ClientApiError) {
        // The API returns one generic message for every credential failure —
        // it must not be reworded here into something that distinguishes an
        // unknown account from a wrong password.
        setError(caught.message);
      } else {
        setError("Unable to reach the server. Please try again.");
      }
      setPending(false);
    }
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
        <Label htmlFor="email">Work email</Label>
        <Input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          placeholder="you@vantagerealty.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          disabled={pending}
          aria-invalid={error ? true : undefined}
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="password">Password</Label>
          <Link
            href="/login"
            className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            Forgot password?
          </Link>
        </div>
        <Input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="current-password"
          placeholder="••••••••••••"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={pending}
          aria-invalid={error ? true : undefined}
        />
      </div>

      <div className="flex items-center gap-2 pt-1">
        <Checkbox id="remember" defaultChecked disabled={pending} />
        <Label htmlFor="remember" className="text-sm font-normal">
          Keep me signed in for 30 days
        </Label>
      </div>

      <Button size="lg" className="w-full" type="submit" disabled={pending}>
        {pending ? (
          <>
            <Loader2 className="size-4 animate-spin" />
            Signing in…
          </>
        ) : (
          <>
            Sign in
            <ArrowRight className="size-4" />
          </>
        )}
      </Button>
    </form>
  );
}
