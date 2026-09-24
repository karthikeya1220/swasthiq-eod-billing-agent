export default function StatCard({ label, value, sublabel, sublabelTone = "blue" }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sublabel != null && sublabel !== "" ? (
        <div className={`stat-sublabel tone-${sublabelTone}`}>{sublabel}</div>
      ) : null}
    </div>
  );
}
