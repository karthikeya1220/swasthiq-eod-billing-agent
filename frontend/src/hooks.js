import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

// Module-level SWR-style cache: navigate between Reconciliation/Analytics for
// the same date without refetching; reload() invalidates the entry first.
const reportCache = new Map();

function snapFor(date) {
  if (!date) return { date: "", report: null, error: "", loading: false };
  const cached = reportCache.get(date);
  return { date, report: cached ?? null, error: "", loading: cached == null };
}

/** Load the deterministic report for `date`, reloading on demand. */
export function useReport(date) {
  const [snap, setSnap] = useState(() => snapFor(date));
  const [tick, setTick] = useState(0);

  // Adjust state when `date` changes (render-phase; avoids effect setState).
  if (snap.date !== date) {
    setSnap(snapFor(date));
  }

  useEffect(() => {
    if (!date) return undefined;
    let cancelled = false;
    api
      .getReport(date)
      .then((data) => {
        if (cancelled) return;
        reportCache.set(date, data);
        setSnap({ date, report: data, error: "", loading: false });
      })
      .catch((err) => {
        if (cancelled) return;
        setSnap((prev) => ({
          date,
          report: reportCache.get(date) ?? prev.report,
          error: err.message,
          loading: false,
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [date, tick]);

  const reload = useCallback(() => {
    if (date) reportCache.delete(date);
    setSnap({ date, report: null, error: "", loading: true });
    setTick((t) => t + 1);
  }, [date]);

  return { report: snap.report, error: snap.error, loading: snap.loading, reload };
}
