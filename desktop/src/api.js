// api.js: כל הדיבור עם שרת ה-Python (FastAPI sidecar) מרוכז כאן.
// הקומפוננטות של React לא יודעות כלום על HTTP: הן רק קוראות לפונקציות האלה.

export const SERVER = "http://127.0.0.1:8765";

async function request(path, options = {}) {
  const res = await fetch(SERVER + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    // FastAPI מחזיר שגיאות בצורה {"detail": "..."}
    let message = `שגיאה ${res.status}`;
    try { message = (await res.json()).detail || message; } catch { /* לא JSON */ }
    throw new Error(message);
  }
  return res;
}

export const getJSON = async (path) => (await request(path)).json();
export const postJSON = async (path, body) =>
  (await request(path, { method: "POST", body: JSON.stringify(body) })).json();

/** מחכה שהשרת יעלה (בפתיחת האפליקציה לוקח לו כמה שניות). */
export async function waitForServer(timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try { return await getJSON("/health"); } catch { await new Promise((r) => setTimeout(r, 700)); }
  }
  throw new Error("שרת האייג'נט לא עלה. נסי לסגור ולפתוח את האפליקציה מחדש.");
}

/**
 * Streaming (SSE) מעל POST.
 * הדפדפן מקבל את התשובה "בחתיכות". כל אירוע מתחיל ב-"data: " ונגמר בשורה ריקה.
 * לכל אירוע שמגיע קוראים ל-onEvent, וכך ה-UI מתעדכן בזמן אמת.
 */
export async function stream(path, body, onEvent) {
  const res = await request(path, { method: "POST", body: JSON.stringify(body) });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {      // אירוע שלם הגיע
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      if (raw.startsWith("data: ")) onEvent(JSON.parse(raw.slice(6)));
    }
  }
}
