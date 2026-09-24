import { Suspense, lazy, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api.js";
import Sidebar from "./components/Sidebar.jsx";

const Reconciliation = lazy(() => import("./pages/Reconciliation.jsx"));
const Analytics = lazy(() => import("./pages/Analytics.jsx"));
const Narrative = lazy(() => import("./pages/Narrative.jsx"));

const PAGE_FALLBACK = (
  <div className="page-loading" role="status">
    Loading…
  </div>
);

export default function App() {
  const [days, setDays] = useState([]);
  const [date, setDate] = useState("");
  const [daysError, setDaysError] = useState("");
  const [daysLoading, setDaysLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .listDays()
      .then((data) => {
        if (cancelled) return;
        const list = data.days || [];
        setDays(list);
        if (list.length > 0) {
          const preferred = list.find((d) => d.date === "2026-07-27") || list[0];
          setDate(preferred.date);
        }
      })
      .catch((err) => {
        if (!cancelled) setDaysError(err.message);
      })
      .finally(() => {
        if (!cancelled) setDaysLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const retry = () => {
    setDaysError("");
    setDaysLoading(true);
    setTick((t) => t + 1);
  };

  let body;
  if (daysError) {
    body = (
      <div className="empty-state" role="alert">
        <h1>Backend unreachable</h1>
        <p>{daysError}</p>
        <p className="muted">
          Start the API with <code>uvicorn app.main:app --port 8000</code> in{" "}
          <code>/backend</code>.
        </p>
        <button type="button" className="button button-primary" onClick={retry}>
          Try again
        </button>
      </div>
    );
  } else if (daysLoading) {
    body = PAGE_FALLBACK;
  } else if (days.length === 0) {
    body = (
      <div className="empty-state">
        <h1>No billing days yet</h1>
        <p>
          Ingest a clinic-day log to <code>POST /api/billing/ingest</code> and it
          will appear here.
        </p>
      </div>
    );
  } else {
    body = (
      <Suspense fallback={PAGE_FALLBACK}>
        <Routes>
          <Route path="/" element={<Navigate to="/reconciliation" replace />} />
          <Route
            path="/reconciliation"
            element={<Reconciliation date={date} days={days} setDate={setDate} />}
          />
          <Route
            path="/analytics"
            element={<Analytics date={date} days={days} setDate={setDate} />}
          />
          <Route
            path="/narrative"
            element={<Narrative date={date} days={days} setDate={setDate} />}
          />
          <Route path="*" element={<Navigate to="/reconciliation" replace />} />
        </Routes>
      </Suspense>
    );
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <Sidebar />
      <main className="app-main" id="main">
        {body}
      </main>
    </div>
  );
}
