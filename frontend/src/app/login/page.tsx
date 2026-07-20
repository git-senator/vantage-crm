import Link from "next/link";
import type { Metadata } from "next";
import { ArrowRight, Quote, ShieldCheck, Sparkles, TrendingUp } from "lucide-react";

import { BrandLockup, BrandMark } from "@/components/shared/brand";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";

export const metadata: Metadata = { title: "Sign in" };

const highlights = [
  {
    icon: Sparkles,
    title: "Lead scoring that explains itself",
    body: "Every score comes with the three signals that drove it, so you know why a buyer is worth calling first.",
  },
  {
    icon: TrendingUp,
    title: "Pipeline forecasting",
    body: "Commission projections update as deals move, with confidence bands you can defend in a Monday review.",
  },
  {
    icon: ShieldCheck,
    title: "Compliance built in",
    body: "Disclosure deadlines and signature status tracked against every transaction file.",
  },
];

export default function LoginPage() {
  return (
    <div className="grid min-h-svh lg:grid-cols-[1fr_1.05fr]">
      {/* ---------------------------------------------------------- form */}
      <div className="flex flex-col px-6 py-8 sm:px-10 lg:px-16">
        <header className="flex items-center justify-between">
          <BrandLockup />
          <span className="text-sm text-muted-foreground">
            Need an account?{" "}
            <Link
              href="/dashboard"
              className="font-medium text-foreground underline-offset-4 hover:underline"
            >
              Request access
            </Link>
          </span>
        </header>

        <div className="flex flex-1 items-center justify-center py-12">
          <div className="w-full max-w-sm">
            <div className="space-y-2">
              <h1 className="text-2xl font-semibold tracking-tight">
                Welcome back
              </h1>
              <p className="text-sm text-muted-foreground">
                Sign in to your Vantage workspace to pick up where you left off.
              </p>
            </div>

            <form className="mt-8 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Work email</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@vantagerealty.com"
                  autoComplete="email"
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
                  type="password"
                  placeholder="••••••••••••"
                  autoComplete="current-password"
                />
              </div>

              <div className="flex items-center gap-2 pt-1">
                <Checkbox id="remember" defaultChecked />
                <Label htmlFor="remember" className="text-sm font-normal">
                  Keep me signed in for 30 days
                </Label>
              </div>

              {/* Navigates straight through — no auth in this prototype. */}
              <Button
                size="lg"
                className="w-full"
                render={<Link href="/dashboard" />}
              >
                Sign in
                <ArrowRight className="size-4" />
              </Button>
            </form>

            <div className="my-6 flex items-center gap-3">
              <Separator className="flex-1" />
              <span className="text-xs text-muted-foreground">OR</span>
              <Separator className="flex-1" />
            </div>

            <div className="grid gap-2">
              <Button variant="outline" size="lg" className="w-full">
                <GoogleGlyph />
                Continue with Google
              </Button>
              <Button variant="outline" size="lg" className="w-full">
                <MicrosoftGlyph />
                Continue with Microsoft
              </Button>
            </div>

            <p className="mt-8 text-center text-xs text-muted-foreground">
              By signing in you agree to the{" "}
              <Link href="/login" className="underline underline-offset-4">
                Terms of Service
              </Link>{" "}
              and{" "}
              <Link href="/login" className="underline underline-offset-4">
                Privacy Policy
              </Link>
              .
            </p>
          </div>
        </div>

        <footer className="text-xs text-muted-foreground">
          © 2026 Vantage Realty Group · UI prototype
        </footer>
      </div>

      {/* --------------------------------------------------------- brand */}
      <aside className="relative hidden overflow-hidden bg-primary text-primary-foreground lg:block">
        {/* Layered radial washes give the panel depth without an image asset. */}
        <div
          aria-hidden
          className="absolute inset-0 opacity-70"
          style={{
            backgroundImage:
              "radial-gradient(60% 55% at 15% 10%, oklch(0.72 0.16 300 / 0.55), transparent 70%), radial-gradient(55% 50% at 90% 85%, oklch(0.68 0.15 210 / 0.5), transparent 70%)",
          }}
        />
        <div
          aria-hidden
          className="absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage:
              "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
            backgroundSize: "56px 56px",
          }}
        />

        <div className="relative flex h-full flex-col justify-between p-12 xl:p-16">
          <div className="max-w-md">
            <BrandMark className="bg-white/15 text-white backdrop-blur-sm" />
            <h2 className="mt-8 text-3xl font-semibold tracking-tight text-balance xl:text-4xl">
              Every listing, lead and closing in one line of sight.
            </h2>
            <p className="mt-4 text-[15px] leading-relaxed text-primary-foreground/75">
              Vantage keeps your brokerage&apos;s pipeline current and tells you
              which conversation to have next.
            </p>
          </div>

          <ul className="my-10 space-y-6">
            {highlights.map((item) => (
              <li key={item.title} className="flex gap-4">
                <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-white/12 backdrop-blur-sm">
                  <item.icon className="size-[18px]" />
                </span>
                <div className="max-w-sm">
                  <p className="text-sm font-medium">{item.title}</p>
                  <p className="mt-1 text-sm leading-relaxed text-primary-foreground/70">
                    {item.body}
                  </p>
                </div>
              </li>
            ))}
          </ul>

          <figure className="max-w-md rounded-2xl bg-white/10 p-6 backdrop-blur-sm">
            <Quote className="size-5 opacity-60" />
            <blockquote className="mt-3 text-[15px] leading-relaxed">
              We cut our lead response time from four hours to eleven minutes.
              The pipeline finally reflects reality.
            </blockquote>
            <figcaption className="mt-4 text-sm text-primary-foreground/70">
              Rowan Ashcroft · Director of Sales, Meridian Properties
            </figcaption>
          </figure>
        </div>
      </aside>
    </div>
  );
}

function GoogleGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
      <path
        fill="#4285F4"
        d="M23.5 12.3c0-.9-.1-1.5-.2-2.2H12v4h6.6c-.1 1.1-.9 2.8-2.5 3.9l3.8 3c2.3-2.1 3.6-5.2 3.6-8.7Z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.8-3c-1 .7-2.4 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-5l-3.9 3C3.4 21.3 7.4 24 12 24Z"
      />
      <path
        fill="#FBBC05"
        d="M5.3 14.3a7.4 7.4 0 0 1 0-4.6l-3.9-3a12 12 0 0 0 0 10.6l3.9-3Z"
      />
      <path
        fill="#EA4335"
        d="M12 4.7c2.2 0 3.7.9 4.5 1.7l3.3-3.2C17.9 1.2 15.2 0 12 0 7.4 0 3.4 2.7 1.4 6.7l3.9 3c1-2.9 3.6-5 6.7-5Z"
      />
    </svg>
  );
}

function MicrosoftGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
      <path fill="#F25022" d="M1 1h10.2v10.2H1z" />
      <path fill="#7FBA00" d="M12.8 1H23v10.2H12.8z" />
      <path fill="#00A4EF" d="M1 12.8h10.2V23H1z" />
      <path fill="#FFB900" d="M12.8 12.8H23V23H12.8z" />
    </svg>
  );
}
