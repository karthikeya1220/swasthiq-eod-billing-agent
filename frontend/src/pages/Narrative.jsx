import { useCallback, useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import PageHeader from "../components/PageHeader.jsx";
import { api } from "../api.js";
import { useReport } from "../hooks.js";

export default function Narrative({ date, days, setDate }) {
  const { report, error: reportError, reload } = useReport(date);
  const [narrative, setNarrative] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [loadedDate, setLoadedDate] = useState(date);
  const genIdRef = useRef(0);

  // Reset narrative state when the selected day changes (render-phase).
  if (loadedDate !== date) {
    setLoadedDate(date);
    setNarrative(null);
    setError("");
    setLoading(false);
  }

  useEffect(() => {
    if (!date) return undefined;
    let cancelled = false;
    // Invalidate any in-flight generate() from the previous day.
    genIdRef.current += 1;
    api
      .getNarrative(date)
      .then((data) => {
        if (cancelled) return;
        setNarrative(data);
        setError("");
      })
      .catch((err) => {
        if (cancelled) return;
        // 404 → not generated yet; anything else is a real error
        if (err.status === 404) setNarrative(null);
        else setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [date]);

  const generate = useCallback(() => {
    if (!date) return;
    const id = ++genIdRef.current;
    setLoading(true);
    setError("");
    api
      .createNarrative(date)
      .then((data) => {
        if (genIdRef.current === id) setNarrative(data);
      })
      .catch((err) => {
        if (genIdRef.current === id) setError(err.message);
      })
      .finally(() => {
        if (genIdRef.current === id) setLoading(false);
      });
  }, [date]);

  const refresh = useCallback(() => {
    genIdRef.current += 1;
    setNarrative(null);
    setError("");
    if (date) {
      api
        .getNarrative(date)
        .then((data) => {
          setNarrative(data);
          setError("");
        })
        .catch((err) => {
          if (err.status === 404) setNarrative(null);
          else setError(err.message);
        });
    }
    reload();
  }, [date, reload]);

  const meta = report?.meta;

  return (
    <div className="page">
      <PageHeader
        title="AI Narrative Summary"
        subtitle={
          meta
            ? `Generated from today's reconciliation — ${meta.clinic_name}`
            : "Grounded in the deterministic EOD report"
        }
        date={date}
        days={days}
        setDate={setDate}
        onRefresh={refresh}
        right={
          <span className="pill pill-purple">
            <Sparkles size={12} /> AI SUGGESTED
          </span>
        }
      />

      {reportError ? (
        <div className="empty-state" role="alert">
          <h2>Could not load report</h2>
          <p>{reportError}</p>
          <button type="button" className="button button-primary" onClick={reload}>
            Try again
          </button>
        </div>
      ) : null}

      <div className="narrative-grid">
        <section className="panel narrative-panel" aria-label="Narrative">
          <div className="chat-bubble">
            <div className="chat-sender">Sent to: Dr. Anand Mehta · WhatsApp</div>
            <div className="chat-body" aria-live="polite">
              {narrative ? (
                narrative.narrative.split("\n").map((line, i) => (
                  <p key={i}>{line || " "}</p>
                ))
              ) : (
                <p className="chat-placeholder">
                  No summary generated for this day yet. Hit “Generate summary”.
                </p>
              )}
            </div>
            {narrative ? (
              <div className="chat-status">
                <span
                  className={`status-badge ${
                    narrative.source === "fallback" ? "status-warn" : "status-ok"
                  }`}
                  title={
                    narrative.llm_error
                      ? `LLM note: ${narrative.llm_error}`
                      : `Model: ${narrative.model}`
                  }
                >
                  {narrative.source === "fallback" ? "FALLBACK" : "SUCCESS"}
                </span>
                <span className="status-model">{narrative.model}</span>
              </div>
            ) : null}
          </div>

          <div className="narrative-actions" aria-live="polite">
            <button
              type="button"
              className="button button-primary"
              onClick={generate}
              disabled={loading || !date}
            >
              {loading
                ? "Generating…"
                : narrative
                  ? "Regenerate summary"
                  : "Generate summary"}
            </button>
            {narrative?.grounded ? (
              <span className="pill pill-green">Every figure grounded ✓</span>
            ) : null}
            {error ? <span className="text-red">{error}</span> : null}
          </div>

          {narrative?.llm_error ? (
            <p className="llm-note muted">
              Model returned an unusable response, so a deterministic grounded
              fallback was used instead. ({narrative.llm_error})
            </p>
          ) : null}
        </section>

        <section className="panel traced-panel" aria-label="Traced figures">
          <h2 className="panel-title">Traced Figures</h2>
          <p className="panel-subtitle">
            Every number above maps to the deterministic report — this is what
            gets auto-checked.
          </p>
          {narrative && narrative.traced_figures.length > 0 ? (
            <ul className="traced-list">
              {narrative.traced_figures.map((figure, i) => (
                <li key={`${figure.figure}-${figure.report_field}-${i}`}>
                  <span className="traced-figure">{figure.figure}</span>
                  <span className="traced-field">{figure.report_field}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted panel-empty">
              Generate a summary to see figure → field provenance.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
