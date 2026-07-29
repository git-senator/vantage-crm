import Link from "next/link";
import type { Metadata } from "next";
import { Quote, ShieldCheck, Sparkles, TrendingUp } from "lucide-react";

import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { BrandLockup, BrandMark } from "@/components/shared/brand";
import { getTranslations } from "@/i18n/server";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage() {
  const t = await getTranslations();

  const highlights = [
    {
      icon: Sparkles,
      title: t("auth.highlight1Title"),
      body: t("auth.highlight1Body"),
    },
    {
      icon: TrendingUp,
      title: t("auth.highlight2Title"),
      body: t("auth.highlight2Body"),
    },
    {
      icon: ShieldCheck,
      title: t("auth.highlight3Title"),
      body: t("auth.highlight3Body"),
    },
  ];

  return (
    <div className="grid min-h-svh lg:grid-cols-[1fr_1.05fr]">
      {/* ---------------------------------------------------------- form */}
      <div className="flex flex-col px-6 py-8 sm:px-10 lg:px-16">
        <header className="flex items-center justify-between">
          <BrandLockup />
          <span className="text-sm text-muted-foreground">
            {t("auth.needAccount")}{" "}
            <Link
              href="/dashboard"
              className="font-medium text-foreground underline-offset-4 hover:underline"
            >
              {t("auth.requestAccess")}
            </Link>
          </span>
        </header>

        <div className="flex flex-1 items-center justify-center py-12">
          <div className="w-full max-w-sm">
            <div className="space-y-2">
              <h1 className="text-2xl font-semibold tracking-tight">
                {t("auth.welcomeBack")}
              </h1>
              <p className="text-sm text-muted-foreground">
                {t("auth.signInSubtitle")}
              </p>
            </div>

            <Suspense fallback={<div className="mt-8 h-[268px]" />}>
              <LoginForm />
            </Suspense>

            <p className="mt-8 text-center text-xs text-muted-foreground">
              {t("auth.termsAgreement")}{" "}
              <Link href="/login" className="underline underline-offset-4">
                {t("auth.termsOfService")}
              </Link>{" "}
              {t("auth.and")}{" "}
              <Link href="/login" className="underline underline-offset-4">
                {t("auth.privacyPolicy")}
              </Link>
              .
            </p>
          </div>
        </div>

        <footer className="text-xs text-muted-foreground">
          {t("auth.footer")}
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
              {t("auth.heroHeadline")}
            </h2>
            <p className="mt-4 text-[15px] leading-relaxed text-primary-foreground/75">
              {t("auth.heroSubtitle")}
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
