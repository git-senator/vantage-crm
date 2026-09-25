import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LOCALE_META } from "@/i18n/config";
import { LanguageProvider } from "@/i18n/language-provider";
import { getLocale } from "@/i18n/server";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "ROSSA CRM — AI Real Estate CRM",
    template: "%s · ROSSA CRM",
  },
  description:
    "An AI-assisted real estate CRM: leads, deals, properties and analytics in one workspace.",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // Resolved from the cookie so the first paint is already in the user's
  // language and `<html lang>` is correct before any JS runs.
  const locale = await getLocale();

  return (
    <html
      lang={LOCALE_META[locale].htmlLang}
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <LanguageProvider initialLocale={locale}>
            <TooltipProvider>{children}</TooltipProvider>
            <Toaster />
          </LanguageProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
