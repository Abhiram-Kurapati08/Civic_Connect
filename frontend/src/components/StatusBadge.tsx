// Status and category badges — the shared vocabulary chips used across cards,
// detail pages, the table and the timeline.
import { CATEGORY_COLORS, CATEGORY_ICONS, CATEGORY_LABELS, STATUS_BADGE_CLASS, STATUS_LABELS } from "@/lib/civic";
import type { Category, Status } from "@/lib/types";
import { cn } from "@/lib/utils";

export function StatusBadge({ status, className }: { status: Status; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
        STATUS_BADGE_CLASS[status],
        className,
      )}
      data-testid={`status-badge-${status}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

export function CategoryChip({ category, className }: { category: Category; className?: string }) {
  const Icon = CATEGORY_ICONS[category];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-[#EAECE4] px-2.5 py-0.5 text-xs font-medium text-[#172B3A]",
        className,
      )}
      data-testid={`category-chip-${category}`}
    >
      <span
        className="size-2 rounded-full"
        style={{ backgroundColor: CATEGORY_COLORS[category] }}
        aria-hidden
      />
      <Icon className="size-3.5" />
      {CATEGORY_LABELS[category]}
    </span>
  );
}
