import { Skeleton } from "@/components/ui/skeleton";

/**
 * Loading state for the listings grid.
 *
 * The shell's generic loading skeleton assumes a two-column detail layout;
 * properties is a card grid, so it would visibly reflow on load. This mirrors
 * the real shape instead — header, stat row, tabs, toolbar, then cards with
 * the same 16:10 image block.
 */
export default function PropertiesLoading() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-44" />
        <Skeleton className="h-4 w-96" />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-[132px] rounded-xl" />
        ))}
      </div>

      <Skeleton className="h-9 w-[420px] max-w-full rounded-lg" />

      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-9 w-80 max-w-full rounded-md" />
        <Skeleton className="h-9 w-40 rounded-md" />
        <Skeleton className="h-9 w-36 rounded-md" />
      </div>

      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, index) => (
          <div key={index} className="overflow-hidden rounded-xl border">
            <Skeleton className="aspect-[16/10] rounded-none" />
            <div className="space-y-3 p-4">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
