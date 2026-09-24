import { useState } from "react";
import { Link } from "react-router-dom";
import { Bell } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { acknowledgeNotifications, fetchNotificationsSummary } from "../api";
import { formatDate } from "../format";
import Badge from "./Badge";

const CATEGORY_TONE = { critical: "critical", opportunity: "good" };

/** The Warning Engine's UI (backend/app/services/notifications.py). Opening
 * the panel IS the acknowledgment. Rendered in the top-right of the header,
 * so the panel opens below and right-aligned to the trigger. */
export default function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const notifications = useQuery({ queryKey: ["notificationsSummary"], queryFn: fetchNotificationsSummary });
  const count = notifications.data?.status === "ok" ? notifications.data.new_critical_count : 0;

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && count > 0) {
      acknowledgeNotifications().then(() => {
        queryClient.invalidateQueries({ queryKey: ["notificationsSummary"] });
      });
    }
  }

  return (
    <span className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="relative rounded-lg p-1.5 text-foreground-subtle hover:bg-surface-sunken hover:text-foreground-strong"
        title="Notifications"
      >
        <Bell className="h-4.5 w-4.5" strokeWidth={2} />
        {count > 0 && (
          <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-critical px-1 text-[10px] font-bold leading-none text-white">
            {count > 9 ? "9+" : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-20 mt-2 w-80 rounded-xl border border-border bg-surface p-3 text-left shadow-xl">
          <div className="text-xs font-medium text-foreground-subtle">
            {count > 0 ? `${count} new important finding${count === 1 ? "" : "s"}` : "No new important findings"}
          </div>
          {notifications.data?.new_findings?.length > 0 && (
            <ul className="mt-2 space-y-2">
              {notifications.data.new_findings.map((f) => (
                <li key={f.id} className="border-t border-border pt-2 first:border-t-0 first:pt-0">
                  <Link
                    to={`/?finding=${encodeURIComponent(f.id)}`}
                    onClick={() => setOpen(false)}
                    className="flex items-start gap-2 text-sm text-foreground-strong hover:underline"
                  >
                    <Badge tone={CATEGORY_TONE[f.category] ?? "accent"} variant="dot" />
                    <span className="min-w-0 flex-1">{f.title}</span>
                  </Link>
                  <span className="text-xs text-foreground-subtle">Detected {formatDate(f.detected)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </span>
  );
}
