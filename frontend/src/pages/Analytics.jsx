import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import PageHeader from "../components/PageHeader.jsx";
import { useReport } from "../hooks.js";
import { formatPaise, formatDate } from "../format.js";

function RankingCard({ title, rows, valueOf, emptyText }) {
  return (
    <section className="panel ranking-panel" aria-label={title}>
      <h2 className="panel-title">{title}</h2>
      {rows.length === 0 ? (
        <p className="muted panel-empty">{emptyText}</p>
      ) : (
        <ol className="ranking-list">
          {rows.slice(0, 5).map((row) => (
            <li key={row.drug_name} className="ranking-row">
              <span className="ranking-rank">{row.rank}</span>
              <span className="ranking-name">{row.drug_name}</span>
              <span className="ranking-value">{valueOf(row)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function Analytics({ date, days, setDate }) {
  const { report, error, loading, reload } = useReport(date);
  const ready = report != null && report.meta.date === date;
  const showLoading = !ready && loading && !error;

  return (
    <div className="page">
      <PageHeader
        title="Analytics"
        subtitle={
          ready
            ? `${report.meta.clinic_subtitle} · ${formatDate(report.meta.date)}`
            : "Revenue by hour and medicine rankings"
        }
        date={date}
        days={days}
        setDate={setDate}
        onRefresh={reload}
      />

      {error ? (
        <div className="empty-state" role="alert">
          <h2>Could not load report</h2>
          <p>{error}</p>
          <button type="button" className="button button-primary" onClick={reload}>
            Try again
          </button>
        </div>
      ) : showLoading ? (
        <div className="page-loading" role="status">
          Loading…
        </div>
      ) : ready ? (
        <ReportBody report={report} />
      ) : null}
    </div>
  );
}

function ReportBody({ report }) {
  const an = report.analytics;
  const meta = report.meta;
  const chartData = an.revenue_by_hour.map((entry) => ({
    hour: entry.hour_label,
    revenue: entry.revenue_paise / 100,
    isPeak: entry.hour === an.peak_hour,
  }));
  const chartSummary =
    an.revenue_by_hour
      .map((h) => `${h.hour_label}: ${formatPaise(h.revenue_paise)}`)
      .join(", ") + (an.peak_hour_range ? `. Peak ${an.peak_hour_range}` : "");

  return (
    <>
      <section className="panel chart-panel" aria-label="Revenue by hour of day">
        <div className="chart-header">
          <h2 className="panel-title">Revenue by Hour of Day</h2>
          {an.peak_hour != null ? (
            <span className="peak-badge">
              Peak: {an.peak_hour_range} — {formatPaise(an.peak_hour_revenue_paise)}
            </span>
          ) : null}
        </div>
        {chartData.length === 0 ? (
          <p className="muted panel-empty">
            No hourly revenue for this day (no sale visits).
          </p>
        ) : (
          <div className="chart-body" role="img" aria-label={`Revenue by hour. ${chartSummary}.`}>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="#eef1f6" />
                <XAxis
                  dataKey="hour"
                  tickLine={false}
                  axisLine={{ stroke: "#e5e7eb" }}
                  tick={{ fill: "#6b7280", fontSize: 11 }}
                />
                <YAxis hide domain={[0, "dataMax"]} />
                <Tooltip
                  formatter={(value) => [
                    formatPaise(Math.round(Number(value) * 100)),
                    "Revenue",
                  ]}
                  labelFormatter={(label) => `Hour: ${label}`}
                  cursor={{ fill: "rgba(37,99,235,0.05)" }}
                />
                <Bar dataKey="revenue" radius={[4, 4, 0, 0]} maxBarSize={48}>
                  {chartData.map((entry) => (
                    <Cell
                      key={entry.hour}
                      fill={entry.isPeak ? "#2563eb" : "#c7d7ff"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <div className="ranking-grid">
        <RankingCard
          title="Top Medicines — by Quantity"
          rows={an.top_medicines_by_quantity}
          valueOf={(row) => `${row.qty} Units`}
          emptyText="No medicine sales this day."
        />
        <RankingCard
          title="Top Medicines — by Revenue"
          rows={an.top_medicines_by_revenue}
          valueOf={(row) => formatPaise(row.revenue_paise)}
          emptyText="No medicine revenue this day."
        />
      </div>

      {meta.data_quality_warnings.length > 0 ? (
        <section className="warnings" aria-label="Data quality notes">
          {meta.data_quality_warnings.map((warning, i) => (
            <p key={`${i}-${warning}`}>{warning}</p>
          ))}
        </section>
      ) : null}
    </>
  );
}
