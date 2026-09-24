const API_BASE = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const message =
      (body && (body.detail?.message || body.detail)) ||
      `Request failed (${response.status})`;
    const error = new Error(typeof message === "string" ? message : "Request failed");
    error.status = response.status;
    error.detail = body?.detail;
    throw error;
  }
  return body;
}

export const api = {
  listDays: () => request("/billing/days"),
  getReport: (date) => request(`/reports/eod/${date}`),
  getNarrative: (date) => request(`/narrative/${date}`),
  createNarrative: (date) => request(`/narrative/${date}`, { method: "POST" }),
  health: () => request("/health"),
};
