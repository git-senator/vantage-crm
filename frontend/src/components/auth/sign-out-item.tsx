"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2, LogOut } from "lucide-react";

import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useTranslation } from "@/i18n/language-provider";
import { apiRequest } from "@/lib/api/client";

/**
 * Sign-out menu item.
 *
 * Navigates to /login regardless of outcome. If the request fails the cookies
 * may survive, but leaving the user on an authenticated screen after they
 * clicked "sign out" is the worse failure — and middleware will bounce them
 * back if the session really is still live.
 */
export function SignOutItem() {
  const router = useRouter();
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);

  async function handleSignOut(event: { preventDefault: () => void }) {
    // Keep the menu open while the request is in flight.
    event.preventDefault();
    if (pending) return;

    setPending(true);
    try {
      await apiRequest("/auth/logout", { method: "POST" });
    } catch {
      // Deliberately ignored — see the component docstring.
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <DropdownMenuItem onSelect={handleSignOut} disabled={pending}>
      {pending ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <LogOut className="size-4" />
      )}
      {pending ? `${t("buttons.signOut")}…` : t("buttons.signOut")}
    </DropdownMenuItem>
  );
}
