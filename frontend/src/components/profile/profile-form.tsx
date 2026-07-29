"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Loader2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ClientApiError } from "@/lib/api/client";
import { updateProfile } from "@/lib/api/profile-client";

/**
 * Edit the signed-in user's own details. Only the fields the API accepts —
 * name, job title, phone. Saving refreshes server components so the sidebar and
 * topbar pick up the new name/avatar without a reload.
 */
export function ProfileForm({
  fullName,
  jobTitle,
  phone,
}: {
  fullName: string;
  jobTitle: string | null;
  phone: string | null;
}) {
  const router = useRouter();
  const [name, setName] = useState(fullName);
  const [title, setTitle] = useState(jobTitle ?? "");
  const [tel, setTel] = useState(phone ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    name.trim() !== fullName ||
    title !== (jobTitle ?? "") ||
    tel !== (phone ?? "");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) {
      setError("Your name cannot be empty.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await updateProfile({
        full_name: name.trim(),
        job_title: title.trim() || null,
        phone: tel.trim() || null,
      });
      toast.success("Profile updated");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Could not save. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  function reset() {
    setName(fullName);
    setTitle(jobTitle ?? "");
    setTel(phone ?? "");
    setError(null);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="full_name">Full name</Label>
          <Input
            id="full_name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={200}
            disabled={pending}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="job_title">Job title</Label>
          <Input
            id="job_title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Managing Broker"
            maxLength={120}
            disabled={pending}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="phone">Phone</Label>
          <Input
            id="phone"
            type="tel"
            value={tel}
            onChange={(e) => setTel(e.target.value)}
            placeholder="(415) 555-0100"
            maxLength={40}
            disabled={pending}
          />
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          onClick={reset}
          disabled={pending || !dirty}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !dirty}>
          {pending && <Loader2 className="size-4 animate-spin" />}
          Save changes
        </Button>
      </div>
    </form>
  );
}
