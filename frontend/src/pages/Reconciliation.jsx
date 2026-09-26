import PageHeader from "../components/PageHeader.jsx";
import StatCard from "../components/StatCard.jsx";
import { useReport } from "../hooks.js";
import { formatPaise } from "../format.js";

const MODE_LABELS = { cash: "Cash", card: "Card", upi: "UPI" };

export default function Reconciliation({ date, days, setDate }) {
  const { report, error, loading, reload } = useReport(date);
  const ready = report != null && report.meta.date === date;
  const showLoading = !ready && loading && !error;

  return (
    <div className="page">
      <PageHeader
        title="EOD Reconciliation"
        subtitle={ready ? report.meta.clinic_subtitle : "Deterministic end-of-day report"}
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
  const rec = report.reconciliation;
  const meta = report.meta;
  const modes = ["cash", "card", "upi"];

  return (
    <>
      <section className="stat-grid" aria-label="Key figures">
        <StatCard
          label="Total Billed"
          value={formatPaise(rec.total_billed_paise)}
          sublabel={`${rec.visits} visits`}
          sublabelTone="blue"
        />
        <StatCard
          label="Total Collected"
          value={formatPaise(rec.total_collected_paise)}
          sublabel={
            rec.collected_pct_of_billed != null
              ? `${rec.collected_pct_of_billed}% of billed`
              : "No sales"
          }
          sublabelTone="green"
        />
        <StatCard
          label="Outstanding"
          value={formatPaise(rec.total_outstanding_paise)}
          sublabel={`${rec.outstanding_visits} pending visits`}
          sublabelTone="orange"
        />
        <StatCard
          label="Refunds"
          value={formatPaise(rec.total_refunded_paise)}
          sublabel={`${rec.refund_visits} ${rec.refund_visits === 1 ? "refund" : "refunds"}`}
          sublabelTone="red"
        />
      </section>

      <section className="panel" aria-label="Payment mode breakdown">
        <h2 className="panel-title">Payment Mode Breakdown</h2>
        <div className="table-wrap">
          <table className="mode-table">
            <thead>
              <tr>
                <th scope="col">Mode</th>
                <th scope="col">Billed</th>
                <th scope="col">Collected</th>
                <th scope="col">Outstanding</th>
              </tr>
            </thead>
            <tbody>
              {modes.map((mode) => {
                const row = rec.by_mode[mode];
                return (
                  <tr key={mode}>
                    <td className="mode-name">{MODE_LABELS[mode]}</td>
                    <td>{formatPaise(row.billed_paise)}</td>
                    <td>{formatPaise(row.collected_paise)}</td>
                    <td>{formatPaise(row.outstanding_paise)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

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
