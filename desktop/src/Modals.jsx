// Modals.jsx: החלונות הקופצים: עזרה, אישור מייל (HITL), והגדרת כלי (אוטנטיקציה).

import { useState } from "react";
import { postJSON } from "./api.js";

function Modal({ title, children, onClose, wide }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className={`modal ${wide ? "wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          {onClose && <button className="btn ghost icon" onClick={onClose}>✕</button>}
        </div>
        {children}
      </div>
    </div>
  );
}

export function HelpModal({ onClose }) {
  return (
    <Modal title="❓ מדריך שימוש" onClose={onClose} wide>
      <div className="help">
        <h4>סריקת לוגים</h4>
        <ul>
          <li><b>גררי תיקייה</b> לאזור הגרירה (או לחצי עליו לבחירה), ואז "שלח"</li>
          <li><code>scan &lt;תיקייה&gt;</code>: קורא רק שורות שנוספו מאז הסריקה הקודמת</li>
          <li><code>scan &lt;תיקייה&gt; --all</code>: סריקה מחדש מההתחלה</li>
          <li>אפשר גם בשפה חופשית: "תבדקי את הלוגים ב-C:\logs"</li>
        </ul>
        <h4>שאלות</h4>
        <ul><li>שאלה על שגיאה, למשל "מה זה ECONNREFUSED?". האייג'נט יחפש ב-Stack Overflow ויצרף מקורות</li></ul>
        <h4>אישור מייל (HITL)</h4>
        <ul><li>אם נמצאה תקלה <b>CRITICAL</b>, ייפתח חלון עם טיוטת מייל. שום מייל לא נשלח בלי לחיצה על "שלח"</li></ul>
        <h4>כלים</h4>
        <ul>
          <li>בפאנל הצד: מצב כל חיבור. "הגדר" פותח טופס להזנת מפתח OpenRouter או פרטי SMTP</li>
          <li>הפרטים נשמרים רק בקובץ <code>.env</code> המקומי</li>
        </ul>
        <h4>איך זה עובד</h4>
        <p className="mono">guardrail → router → scan → filter → classify → analyze ⇄ evaluator → report → (CRITICAL? מייל + אישור)</p>
        <p className="muted small">בזמן הריצה רואים כל נוד שמתחיל ומסתיים. בסוף כל משימה מוצגת העלות, ולחיצה עליה מציגה פירוט לפי נוד.</p>
      </div>
    </Modal>
  );
}

export function ApprovalModal({ email, onAnswer }) {
  return (
    <Modal title="📧 נמצאה תקלה קריטית: לשלוח התראה?">
      <div className="email-preview">
        <div><b>אל:</b> {email.to}</div>
        <div><b>נושא:</b> {email.subject}</div>
        {email.mode === "outbox" && <div className="note">מצב בדיקה: המייל יישמר בתיקייה outbox ולא יישלח באמת</div>}
        <pre>{email.body}</pre>
      </div>
      <div className="modal-actions">
        <button className="btn primary" onClick={() => onAnswer(true)}>📤 שלח</button>
        <button className="btn" onClick={() => onAnswer(false)}>בטל</button>
      </div>
    </Modal>
  );
}

export function AuthModal({ tool, onClose }) {
  // ערכים התחלתיים: שדות רגילים מציגים את הערך הקיים, שדות סודיים מתחילים ריקים
  const [values, setValues] = useState(Object.fromEntries(tool.fields.map((f) => [f.key, f.secret ? "" : f.value || ""])));
  const [save, setSave] = useState(true);
  const [message, setMessage] = useState("");

  async function submit(e) {
    e.preventDefault();
    try {
      const r = await postJSON(`/tools/${tool.name}/auth`, { values, save });
      setMessage(r.updated.length ? `✅ עודכן: ${r.updated.join(", ")}` : "לא שונה כלום");
      setTimeout(onClose, 900);
    } catch (err) { setMessage(`❌ ${err.message}`); }
  }

  return (
    <Modal title={`🔐 הגדרת ${tool.title}`} onClose={onClose}>
      <form className="auth-form" onSubmit={submit}>
        {tool.name === "email" && (
          <p className="muted small">ב-Gmail צריך "סיסמת אפליקציה" (App Password), לא את הסיסמה הרגילה.</p>
        )}
        {tool.fields.map((f) => (
          <label key={f.key}>
            {f.label}
            <input type={f.secret ? "password" : "text"} dir="ltr" value={values[f.key]}
                   placeholder={f.secret && f.set ? "•••••• (מוגדר. השאירי ריק כדי לא לשנות)" : ""}
                   onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
          </label>
        ))}
        <label className="check"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> לשמור גם בקובץ .env</label>
        {message && <div className="small">{message}</div>}
        <div className="modal-actions">
          <button className="btn primary">שמירה</button>
          <button type="button" className="btn" onClick={onClose}>ביטול</button>
        </div>
      </form>
    </Modal>
  );
}
