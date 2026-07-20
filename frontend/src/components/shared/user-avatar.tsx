import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { TeamMember } from "@/types";
import { cn } from "@/lib/utils";

const sizes = {
  xs: "size-6 text-[10px]",
  sm: "size-8 text-[11px]",
  md: "size-9 text-xs",
  lg: "size-12 text-sm",
  xl: "size-20 text-xl",
} as const;

/**
 * Initials avatar tinted from the member's stored hue. No image assets, so the
 * prototype stays self-contained and every person is still visually distinct.
 */
export function UserAvatar({
  user,
  size = "sm",
  className,
}: {
  user: TeamMember;
  size?: keyof typeof sizes;
  className?: string;
}) {
  return (
    <Avatar className={cn(sizes[size], className)}>
      <AvatarFallback
        className="font-medium"
        style={{
          backgroundColor: `oklch(0.92 0.055 ${user.hue})`,
          color: `oklch(0.42 0.13 ${user.hue})`,
        }}
      >
        {user.initials}
      </AvatarFallback>
    </Avatar>
  );
}

export function AvatarStack({
  users,
  max = 4,
  size = "xs",
}: {
  users: TeamMember[];
  max?: number;
  size?: keyof typeof sizes;
}) {
  const shown = users.slice(0, max);
  const overflow = users.length - shown.length;

  return (
    <div className="flex items-center -space-x-2">
      {shown.map((user) => (
        <UserAvatar
          key={user.id}
          user={user}
          size={size}
          className="ring-2 ring-card"
        />
      ))}
      {overflow > 0 && (
        <span
          className={cn(
            sizes[size],
            "grid place-items-center rounded-full bg-muted font-medium text-muted-foreground ring-2 ring-card",
          )}
        >
          +{overflow}
        </span>
      )}
    </div>
  );
}
