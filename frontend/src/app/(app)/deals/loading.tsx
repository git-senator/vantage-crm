import { Skeleton } from "@/components/ui/skeleton";

/**
 * Loading state for the Kanban board.
 *
 * The shell's generic skeleton assumes a two-column detail layout and would
 * visibly reflow into a horizontal column strip. This mirrors the real shape:
 * header, stat row, then five columns of cards.
 */
export default function DealsLoading() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-4 w-96" />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-[132px] rounded-xl" />
        ))}
      </div>

      <div className="scrollbar-slim -mx-4 overflow-hidden px-4 pb-4 md:-mx-6 md:px-6">
        <div className="flex min-w-max gap-4">
          {Array.from({ length: 5 }).map((_, column) => (
            <div
              key={column}
              className="flex w-[300px] shrink-0 flex-col gap-2.5 rounded-xl bg-muted/40 p-2.5"
            >
              <Skeleton className="mx-1 h-5 w-40" />
              {Array.from({ length: 3 - (column % 2) }).map((_, card) => (
                <Skeleton key={card} className="h-[104px] rounded-xl" />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
