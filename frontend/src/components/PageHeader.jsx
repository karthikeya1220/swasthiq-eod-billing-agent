import { CalendarDays, RefreshCw } from "lucide-react";
import { formatDate } from "../format.js";

export default function PageHeader({
  title,
  subtitle,
  date,
  days,
  setDate,
  onRefresh,
  right,
}) {
  return (
    <header className="page-header">
      <div className="page-header-left">
        <h1>{title}</h1>
        <p className="page-subtitle">{subtitle}</p>
      </div>
      <div className="page-header-right">
        {right}
        {days?.length > 0 ? (
          <label className="date-chip" title="Clinic day">
            <CalendarDays size={14} />
            <select
              value={date}
              onChange={(e) => setDate?.(e.target.value)}
              aria-label="Select clinic day"
            >
              {days.map((d) => (
                <option key={d.date} value={d.date}>
                  {formatDate(d.date)}
                  {d.rows_rejected ? ` (${d.rows_rejected} rejected)` : ""}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {onRefresh ? (
          <button
            type="button"
            className="icon-button"
            onClick={onRefresh}
            title="Refresh"
            aria-label="Refresh"
          >
            <RefreshCw size={14} />
          </button>
        ) : null}
      </div>
    </header>
  );
}
