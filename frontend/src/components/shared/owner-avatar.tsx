import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { OwnerSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const sizes = {
  xs: "size-6 text-[10px]",
  sm: "size-8 text-[11px]",
  md: "size-9 text-xs",
} as const;

/**
 * Initials avatar for an API `OwnerSummary` (author, actor, uploader). Tinted
 * from the stored hue, no image assets — the same treatment as UserAvatar, but
 * keyed off the API shape (`avatar_hue`) rather than the prototype's TeamMember.
 */
export function OwnerAvatar({
  owner,
  size = "sm",
  className,
}: {
  owner: OwnerSummary | null;
  size?: keyof typeof sizes;
  className?: string;
}) {
  const initials = owner?.initials ?? "—";
  const hue = owner?.avatar_hue ?? 240;
  return (
    <Avatar className={cn(sizes[size], className)}>
      <AvatarFallback
        className="font-medium"
        style={{
          backgroundColor: `oklch(0.92 0.055 ${hue})`,
          color: `oklch(0.42 0.13 ${hue})`,
        }}
      >
        {initials}
      </AvatarFallback>
    </Avatar>
  );
}
