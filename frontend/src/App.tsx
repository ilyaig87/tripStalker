import { useEffect, useState } from "react";
import { createTrack, deleteTrack, listTracks, refreshTracks, resetTrack, type Track } from "./api";

const STATUS = {
  Active: { label: "במעקב", cls: "status--active" },
  Triggered: { label: "ירידת מחיר!", cls: "status--triggered" },
  Expired: { label: "פג תוקף", cls: "status--expired" },
} as const;

// status spine color (CSS var) per status
const SPINE: Record<string, string> = {
  Active: "var(--teal)",
  Triggered: "var(--down)",
  Expired: "var(--ink-faint)",
};

const PROVIDER_LABEL: Record<string, string> = {
  holidayfinder: "HolidayFinder",
  travelist: "Travelist",
  booking: "Booking",
};

const THEMES = [
  { id: "beach", label: "חוף", swatch: "#15605a" },
  { id: "midnight", label: "חצות", swatch: "#e3b657" },
  { id: "sunset", label: "שקיעה", swatch: "#d2603a" },
] as const;

function sym(currency: string) {
  return currency === "USD" ? "$" : currency === "ILS" ? "₪" : "";
}
function money(value: string | null, currency: string) {
  if (value === null) return "—";
  return `${sym(currency)}${Number(value).toLocaleString()}`;
}

// "≈ ₪14,200" — shekel equivalent of a USD price (null if not applicable)
function ilsApprox(value: string | null, currency: string, rate: number | null) {
  if (value === null || currency !== "USD" || !rate) return null;
  return `≈ ₪${Math.round(Number(value) * rate).toLocaleString()}`;
}

// "2026-09-15" -> "15.09"
function dm(iso: string | null) {
  if (!iso) return "?";
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
}
function nights(a: string | null, b: string | null) {
  if (!a || !b) return null;
  return Math.round((new Date(b).getTime() - new Date(a).getTime()) / 86_400_000);
}

// "נבדק לפני 3 ש׳" — relative time of the last price check
function lastChecked(iso: string | null) {
  if (!iso) return "טרם נבדק";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (min < 1) return "נבדק כעת";
  if (min < 60) return `נבדק לפני ${min} ד׳`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `נבדק לפני ${hr} ש׳`;
  return `נבדק לפני ${Math.round(hr / 24)} י׳`;
}

// "2-adults,1-children" -> { persons, label }
function occupancy(cfg: string | null) {
  if (!cfg) return { persons: 1, label: "" };
  let persons = 0;
  const parts: string[] = [];
  for (const p of cfg.split(",")) {
    const n = parseInt(p, 10);
    if (Number.isNaN(n)) continue;
    persons += n;
    if (p.includes("adult")) parts.push(`${n} ${n === 1 ? "מבוגר" : "מבוגרים"}`);
    else if (p.includes("child")) parts.push(`${n} ${n === 1 ? "ילד" : "ילדים"}`);
    else if (p.includes("room")) parts.push(`${n} ${n === 1 ? "חדר" : "חדרים"}`);
  }
  return { persons: persons || 1, label: parts.join(" · ") };
}

type Delta = { dir: "up" | "down" | "flat"; amount: string; pct: string };

function priceDelta(initial: string | null, current: string | null): Delta | null {
  if (initial === null || current === null) return null;
  const i = Number(initial);
  const c = Number(current);
  if (!(i > 0)) return null;
  const diff = c - i;
  const amount = Math.abs(diff).toLocaleString(undefined, { maximumFractionDigits: 0 });
  const pct = Math.abs((diff / i) * 100).toFixed(1);
  if (diff < -0.5) return { dir: "down", amount, pct };
  if (diff > 0.5) return { dir: "up", amount, pct };
  return { dir: "flat", amount: "0", pct: "0" };
}

function Stat({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-ico">{icon}</span>
      <span>
        <span className="stat-label" style={{ display: "block" }}>
          {label}
        </span>
        <span className="stat-val">{value}</span>
      </span>
    </div>
  );
}

function PlaneMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M2.5 13.5l19-8.5-4 18-5-6-5 3 0-4 9-7-12 4z"
        fill="currentColor"
        stroke="currentColor"
        strokeWidth="0.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function App() {
  const [email, setEmail] = useState("");
  const [url, setUrl] = useState("");
  const [tracks, setTracks] = useState<Track[]>([]);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("ts_theme") || "beach");
  const [usdIls, setUsdIls] = useState<number | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ts_theme", theme);
  }, [theme]);

  // Live USD→ILS rate (free, no key) so we can show ₪ alongside $ prices.
  useEffect(() => {
    fetch("https://open.er-api.com/v6/latest/USD")
      .then((r) => r.json())
      .then((d) => d?.rates?.ILS && setUsdIls(d.rates.ILS))
      .catch(() => {});
  }, []);

  async function refresh(forEmail: string) {
    if (!forEmail) return;
    try {
      setTracks(await listTracks(forEmail));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    const saved = localStorage.getItem("ts_email");
    if (saved) {
      setEmail(saved);
      refresh(saved);
    }
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await createTrack(email, url);
      localStorage.setItem("ts_email", email);
      setUrl("");
      await refresh(email);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function onDelete(id: number) {
    await deleteTrack(id);
    await refresh(email);
  }

  async function onReset(id: number) {
    await resetTrack(id); // make the current price the new baseline
    await refresh(email);
  }

  async function onCheckNow() {
    if (!email) return;
    setError(null);
    setChecking(true);
    try {
      setTracks(await refreshTracks(email)); // re-fetches live prices for all my tracks
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  const dropped = tracks.filter((t) => {
    const d = priceDelta(t.initial_price, t.current_price);
    return d?.dir === "down";
  }).length;

  return (
    <div dir="rtl" className="wrap font-body">
      {/* header */}
      <header>
        <div className="header-top">
          <div className="brand">
            <span className="brand-mark">
              <PlaneMark />
            </span>
            <h1 className="brand-title font-display">
              Trip<span className="brand-accent">Stalker</span>
            </h1>
          </div>
          <div className="theme-picker" role="group" aria-label="ערכת עיצוב">
            {THEMES.map((th) => (
              <button
                key={th.id}
                type="button"
                className="swatch"
                title={th.label}
                aria-pressed={theme === th.id}
                style={{ background: th.swatch }}
                onClick={() => setTheme(th.id)}
              />
            ))}
          </div>
        </div>
        <p className="tagline">הדביקו קישור למלון או חבילת נופש — ואנחנו נשגיח על המחיר במקומכם.</p>

        {tracks.length > 0 && (
          <div className="summary">
            <span className="summary-item">
              <span className="summary-num">{tracks.length}</span>
              <span className="summary-label">מעקבים פעילים</span>
            </span>
            <span className="summary-item">
              <span className="summary-num" style={{ color: dropped ? "var(--down)" : "var(--ink)" }}>
                {dropped}
              </span>
              <span className="summary-label">ירדו במחיר</span>
            </span>
          </div>
        )}
      </header>

      {/* add form */}
      <form onSubmit={onSubmit} className="panel">
        <div
          style={{
            display: "grid",
            gap: "0.85rem",
            gridTemplateColumns: "minmax(0,1fr) minmax(0,1.7fr) auto",
            alignItems: "end",
          }}
        >
          <label className="field">
            <span className="field-label">אימייל</span>
            <input
              className="input"
              type="email"
              required
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">קישור להצעה</span>
            <input
              className="input"
              type="url"
              required
              placeholder="HolidayFinder · Booking · Travelist…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </label>
          <button className="btn-primary" type="submit" disabled={loading}>
            {loading ? "מוסיף…" : "עקבו אחרי המחיר"}
          </button>
        </div>
      </form>

      {error && <div className="alert">{error}</div>}

      <div className="section-row">
        <h2 className="section-label" style={{ margin: 0 }}>
          המעקבים שלי
        </h2>
        {tracks.length > 0 && (
          <button className="btn-check" onClick={onCheckNow} disabled={checking}>
            {checking ? "בודק מחירים…" : "🔄 בדוק עכשיו"}
          </button>
        )}
      </div>

      {tracks.length === 0 ? (
        <div className="empty">עדיין אין מעקבים — הדביקו קישור למעלה כדי להתחיל ✈️</div>
      ) : (
        <div style={{ display: "grid", gap: "1.1rem" }}>
          {tracks.map((t, idx) => {
            const { persons, label: occ } = occupancy(t.room_config);
            const n = nights(t.check_in_date, t.check_out_date);
            const perPax =
              t.current_price && persons > 1
                ? `${sym(t.currency)}${(Number(t.current_price) / persons).toLocaleString(undefined, {
                    maximumFractionDigits: 0,
                  })}`
                : null;
            const delta = priceDelta(t.initial_price, t.current_price);
            const st = STATUS[t.status] ?? STATUS.Active;
            const gone = !t.available;

            return (
              <article
                key={t.id}
                className={`ticket reveal${gone ? " ticket--gone" : ""}`}
                style={{
                  ["--spine" as string]: gone ? "var(--up)" : SPINE[t.status],
                  animationDelay: `${idx * 70}ms`,
                }}
              >
                <div className="ticket-head">
                  <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                    <span className="badge">{PROVIDER_LABEL[t.provider] ?? t.provider}</span>
                    {gone ? (
                      <span className="status status--gone">⚠ לא זמינה יותר</span>
                    ) : (
                      <span className={`status ${st.cls}`}>{st.label}</span>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <button
                      className="reset-btn"
                      onClick={() => onReset(t.id)}
                      title="אפס את הבסיס למחיר הנוכחי"
                    >
                      ↺ אפס
                    </button>
                    <button className="delete-btn" onClick={() => onDelete(t.id)} title="מחיקה">
                      הסר ✕
                    </button>
                  </div>
                </div>

                <h3 className="hotel">
                  {t.hotel_name || t.destination || PROVIDER_LABEL[t.provider] || "מעקב"}
                </h3>

                <div className="stats">
                  {t.destination && <Stat icon="📍" label="יציאה" value={t.destination} />}
                  <Stat
                    icon="📅"
                    label="תאריכים"
                    value={`${dm(t.check_in_date)} – ${dm(t.check_out_date)}${n ? ` · ${n} ל׳` : ""}`}
                  />
                  {occ && <Stat icon="👥" label="תפוסה" value={occ} />}
                </div>

                {gone && (
                  <div className="note-gone">
                    <span aria-hidden>⚠️</span>
                    ההצעה לא נמצאה בבדיקה האחרונה — ייתכן שנמכרה או הוסרה. המחיר הוא האחרון שנשמר.
                  </div>
                )}

                <div className="foot">
                  <div>
                    <div className="price-now">
                      <span className="price-cur tnum">
                        {money(t.current_price, t.currency)}
                        <small>סה״כ</small>
                      </span>
                      {!gone && delta && delta.dir !== "flat" && (
                        <span className={`delta delta--${delta.dir} tnum`}>
                          {delta.dir === "down" ? "↓ ירד" : "↑ עלה"} {sym(t.currency)}
                          {delta.amount} ({delta.pct}%)
                        </span>
                      )}
                      {!gone && delta && delta.dir === "flat" && (
                        <span className="delta delta--flat">ללא שינוי</span>
                      )}
                    </div>
                    {ilsApprox(t.current_price, t.currency, usdIls) && (
                      <div className="ils-approx tnum">{ilsApprox(t.current_price, t.currency, usdIls)}</div>
                    )}
                    <div className="price-sub tnum">
                      {perPax && <>{perPax} לאדם · </>}
                      <span className="price-reg">נרשם ב-{money(t.initial_price, t.currency)}</span>
                    </div>
                    {t.hotel_portion && t.flight_portion && (
                      <div className="breakdown tnum">
                        🏨 מלון {money(t.hotel_portion, t.currency)} · ✈️ טיסה {money(t.flight_portion, t.currency)}
                      </div>
                    )}
                    <div className="checked-at">🕐 {lastChecked(t.last_checked_at)}</div>
                  </div>

                  <a className="offer-link" href={t.raw_url} target="_blank" rel="noreferrer">
                    פתחו את ההצעה
                    <span aria-hidden>↗</span>
                  </a>
                </div>

                {t.alt_price && (
                  <a className="alt-suggest" href={t.alt_url ?? t.raw_url} target="_blank" rel="noreferrer">
                    💡 אותו מלון זול יותר ב-<b>{dm(t.alt_check_in)}–{dm(t.alt_check_out)}</b>:{" "}
                    <b>{money(t.alt_price, t.currency)}</b>
                    {t.current_price && Number(t.current_price) > Number(t.alt_price) && (
                      <span className="alt-save">
                        {" "}
                        חיסכון {sym(t.currency)}
                        {Math.round(Number(t.current_price) - Number(t.alt_price)).toLocaleString()}
                      </span>
                    )}
                    <span aria-hidden> ↗</span>
                  </a>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
