import { useEffect, useState } from "react";
import {
  type AuthUser,
  clearToken,
  createTrack,
  deleteTrack,
  getToken,
  getTrack,
  listTracks,
  login,
  me,
  type PriceHistoryPoint,
  refreshTracks,
  register,
  resetTrack,
  setToken,
  type Track,
} from "./api";

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
  { id: "riviera", label: "ריביירה", swatch: "#0f766e" },
  { id: "santorini", label: "סנטוריני", swatch: "#2563a6" },
  { id: "sahara", label: "סהרה", swatch: "#c2632f" },
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

// The backend sends NAIVE UTC timestamps (no "Z"/offset). Parse them as UTC so
// the browser converts to the user's local time instead of assuming local.
function parseUTC(iso: string): Date {
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasTz ? iso : iso + "Z");
}
function hhmm(d: Date): string {
  return d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });
}
function ddmm(d: Date): string {
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
}

// Last price check: "זה עתה" / "לפני N דקות" for the last hour; otherwise
// "היום/אתמול בשעה HH:MM", and for older — the date itself (dd/mm) + time.
function lastChecked(iso: string | null) {
  if (!iso) return "טרם נבדק";
  const d = parseUTC(iso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return "נבדק זה עתה";
  const min = Math.round(sec / 60);
  if (min < 60) return `נבדק לפני ${min} ${min === 1 ? "דקה" : "דקות"}`;
  const now = new Date();
  const yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return `נבדק היום בשעה ${hhmm(d)}`;
  if (d.toDateString() === yest.toDateString()) return `נבדק אתמול בשעה ${hhmm(d)}`;
  return `נבדק ב-${ddmm(d)} בשעה ${hhmm(d)}`;
}

// Keep only the points where the price changed (+ the first). For each change,
// also work out how much came from the hotel vs the flight (when known).
function changeLog(points: PriceHistoryPoint[]) {
  const out: {
    date: string;
    price: number;
    delta: number | null;
    hotel: number | null;
    flight: number | null;
    hotelDelta: number | null;
    flightDelta: number | null;
  }[] = [];
  let prev: PriceHistoryPoint | null = null;
  for (const p of points) {
    const v = Number(p.price);
    if (prev === null || v !== Number(prev.price)) {
      const portionDelta = (cur: string | null, before: string | null) =>
        cur != null && before != null ? Number(cur) - Number(before) : null;
      out.push({
        date: p.checked_at,
        price: v,
        delta: prev === null ? null : v - Number(prev.price),
        hotel: p.hotel_portion != null ? Number(p.hotel_portion) : null,
        flight: p.flight_portion != null ? Number(p.flight_portion) : null,
        hotelDelta: prev ? portionDelta(p.hotel_portion, prev.hotel_portion) : null,
        flightDelta: prev ? portionDelta(p.flight_portion, prev.flight_portion) : null,
      });
      prev = p;
    }
  }
  return out;
}

function signed(n: number, currency: string) {
  const s = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${s}${sym(currency)}${Math.abs(Math.round(n)).toLocaleString()}`;
}

type PkgLeg = { date?: string; airline?: string; dep?: string; arr?: string; stops?: number };

// Parse the package flight legs (HolidayFinder) stored as JSON.
function packageFlight(jsonStr: string | null): { out: PkgLeg | null; back: PkgLeg | null } | null {
  if (!jsonStr) return null;
  try {
    const d = JSON.parse(jsonStr);
    if (!d.out && !d.back) return null;
    return { out: d.out || null, back: d.back || null };
  } catch {
    return null;
  }
}

// Cheapest fares recently seen on a route, by source (Travelpayouts flight radar).
// Sorted cheapest-first by the backend.
type CompareOffer = {
  agency: string;
  price: number;
  url: string | null;
  currency: string;
  note?: string;
};

function compareOffers(jsonStr: string | null): CompareOffer[] {
  if (!jsonStr) return [];
  try {
    const d = JSON.parse(jsonStr);
    return Array.isArray(d)
      ? d.filter((o) => o && typeof o.price === "number" && typeof o.agency === "string")
      : [];
  } catch {
    return [];
  }
}

type HotelMeta = {
  stars?: number;
  review_score?: number;
  review_count?: number;
  room?: string;
  board?: string;
  refundable_until?: string;
  free_cancellation?: boolean;
  tags?: string[];
  maps_url?: string;
  highlight?: string;
  photo?: string;
  photos?: string[];
  flight_kind?: string;
  airline?: string;
  flight_label?: string;
  luggage?: string; // HolidayFinder fare tier: naked | withTrolley | withCib | withBoth
};

// Parse the rich hotel metadata (HolidayFinder) stored as JSON.
function hotelMeta(jsonStr: string | null): HotelMeta | null {
  if (!jsonStr) return null;
  try {
    return JSON.parse(jsonStr) as HotelMeta;
  } catch {
    return null;
  }
}

// HolidayFinder flight fare tier → Hebrew label. "Cib" = checked-in baggage.
// HF inconsistently prefixes some tiers with "A" (AwithTrolley / AwithBoth), so
// we strip a leading "A" before matching.
const LUGGAGE_LABEL: Record<string, string> = {
  naked: "🎒 ללא כבודה",
  withTrolley: "🧳 טרולי",
  withCib: "🧳 מזוודה",
  withBoth: "🧳 טרולי + מזוודה",
};
function luggageLabel(tier?: string): string | null {
  if (!tier) return null;
  const key = tier.replace(/^A(?=with)/, ""); // AwithTrolley → withTrolley
  return LUGGAGE_LABEL[key] ?? `🧳 ${tier}`;
}

type Weather = { tmax: number; tmin: number };

// Typical weather at a destination for the travel month — open-meteo, keyless.
// Uses last year's same dates (archive) as a "typical" estimate for future trips.
async function fetchWeather(city: string, checkIn: string, checkOut: string | null): Promise<Weather | null> {
  const geo = await fetch(
    `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(city)}&count=1&language=en&format=json`
  ).then((r) => r.json());
  const loc = geo?.results?.[0];
  if (!loc) return null;
  const lastYear = (s: string) => {
    const d = new Date(s);
    d.setFullYear(d.getFullYear() - 1);
    return d.toISOString().slice(0, 10);
  };
  const start = lastYear(checkIn);
  const end = lastYear(checkOut || checkIn);
  const a = await fetch(
    `https://archive-api.open-meteo.com/v1/archive?latitude=${loc.latitude}&longitude=${loc.longitude}` +
      `&start_date=${start}&end_date=${end}&daily=temperature_2m_max,temperature_2m_min&timezone=auto`
  ).then((r) => r.json());
  const maxes: number[] = (a?.daily?.temperature_2m_max || []).filter((x: number) => x != null);
  const mins: number[] = (a?.daily?.temperature_2m_min || []).filter((x: number) => x != null);
  if (!maxes.length) return null;
  const avg = (arr: number[]) => Math.round(arr.reduce((s, x) => s + x, 0) / arr.length);
  return { tmax: avg(maxes), tmin: avg(mins) };
}

// "Good deal?" — position of the current price within its all-time low–high range.
function dealBadge(t: Track): { cls: string; label: string } | null {
  if (t.price_low == null || t.price_high == null || t.current_price == null) return null;
  const low = Number(t.price_low);
  const high = Number(t.price_high);
  const cur = Number(t.current_price);
  if (high - low < Math.max(1, low * 0.005)) return null; // not enough range yet
  const pct = (cur - low) / (high - low);
  if (pct <= 0.15) return { cls: "deal--great", label: "🟢 מחיר מעולה — קרוב לשפל" };
  if (pct >= 0.85) return { cls: "deal--high", label: "🔴 יקר — קרוב לשיא" };
  return { cls: "deal--mid", label: "🟡 מחיר ממוצע" };
}

// Browsable hotel photo gallery for the postcard header.
function PhotoCarousel({ photos, city, stars }: { photos: string[]; city?: string | null; stars?: number }) {
  const [i, setI] = useState(0);
  const n = photos.length;
  const go = (d: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setI((p) => (p + d + n) % n);
  };
  return (
    <div className="ticket-photo" style={{ backgroundImage: `url(${photos[i]})` }}>
      {stars ? (
        <span className="ticket-photo-stars" aria-label={`${stars} כוכבים`}>
          {"★".repeat(stars)}
        </span>
      ) : null}
      {city && <span className="ticket-photo-city">{city}</span>}
      {n > 1 && (
        <>
          <button className="photo-nav photo-nav--prev" onClick={go(-1)} aria-label="תמונה קודמת">
            ‹
          </button>
          <button className="photo-nav photo-nav--next" onClick={go(1)} aria-label="תמונה הבאה">
            ›
          </button>
          <div className="photo-dots" aria-hidden>
            {photos.map((_, k) => (
              <span
                key={k}
                className={`photo-dot${k === i ? " is-on" : ""}`}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setI(k);
                }}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Sparkline({ points }: { points: PriceHistoryPoint[] }) {
  const vals = points.map((p) => Number(p.price));
  if (vals.length < 2) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const W = 240;
  const H = 46;
  const pad = 5;
  const coords = vals
    .map((v, i) => {
      const x = pad + (i / (vals.length - 1)) * (W - 2 * pad);
      const y = pad + (1 - (v - min) / span) * (H - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const color = vals[vals.length - 1] <= vals[0] ? "var(--down)" : "var(--up)";
  return (
    <svg className="sparkline" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden>
      <polyline points={coords} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
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

// The signature split-flap price: each character on its own mechanical tile.
function FlapPrice({ text }: { text: string }) {
  return (
    <span className="flap-row" aria-label={text}>
      {Array.from(text).map((ch, i) =>
        ch === " " ? (
          <span key={i} className="flap-gap" aria-hidden />
        ) : (
          <span key={i} className="flap-tile" style={{ animationDelay: `${i * 45}ms` }} aria-hidden>
            {ch}
          </span>
        )
      )}
    </span>
  );
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

// Login / register gate, shown until the user has a valid token.
function AuthScreen({
  theme,
  onTheme,
  onAuthed,
}: {
  theme: string;
  onTheme: (id: string) => void;
  onAuthed: (token: string, user: AuthUser) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = mode === "login" ? await login(email, password) : await register(email, password);
      onAuthed(res.access_token, res.user);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div dir="rtl" className="wrap font-body auth-wrap">
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
                onClick={() => onTheme(th.id)}
              />
            ))}
          </div>
        </div>
        <p className="tagline">לוח היציאות שלך — התחברו כדי לעקוב אחרי מחירי טיסות ומלונות.</p>
      </header>

      <form onSubmit={submit} className="panel auth-card">
        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={`auth-tab${mode === "login" ? " is-active" : ""}`}
            onClick={() => {
              setMode("login");
              setErr(null);
            }}
          >
            כניסה
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            className={`auth-tab${mode === "register" ? " is-active" : ""}`}
            onClick={() => {
              setMode("register");
              setErr(null);
            }}
          >
            הרשמה
          </button>
        </div>

        <label className="field">
          <span className="field-label">אימייל</span>
          <input
            className="input"
            type="email"
            required
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field" style={{ marginTop: "0.9rem" }}>
          <span className="field-label">סיסמה</span>
          <input
            className="input"
            type="password"
            required
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={mode === "register" ? 8 : undefined}
            placeholder={mode === "register" ? "לפחות 8 תווים" : "••••••••"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {err && (
          <div className="alert" style={{ marginTop: "0.95rem" }}>
            {err}
          </div>
        )}

        <button
          className="btn-primary"
          type="submit"
          disabled={busy}
          style={{ width: "100%", marginTop: "1.15rem" }}
        >
          {busy ? "רגע…" : mode === "login" ? "כניסה ←" : "צרו חשבון ←"}
        </button>

        <p className="auth-switch">
          {mode === "login" ? "אין לכם חשבון עדיין?" : "כבר רשומים?"}{" "}
          <button
            type="button"
            className="auth-link"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setErr(null);
            }}
          >
            {mode === "login" ? "להרשמה" : "לכניסה"}
          </button>
        </p>
      </form>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [url, setUrl] = useState("");
  const [tracks, setTracks] = useState<Track[]>([]);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<string>(() => {
    const saved = localStorage.getItem("ts_theme");
    return saved && ["riviera", "santorini", "sahara"].includes(saved) ? saved : "riviera";
  });
  const [usdIls, setUsdIls] = useState<number | null>(null);
  const [openHistory, setOpenHistory] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<number | null>(null); // which card is opened full-width
  const [history, setHistory] = useState<Record<number, PriceHistoryPoint[]>>({});
  const [weather, setWeather] = useState<Record<string, Weather | "loading" | null>>({});

  // Fetch typical destination weather (keyless) for any track that has a city.
  useEffect(() => {
    for (const t of tracks) {
      if (!t.destination_city || !t.check_in_date) continue;
      const key = `${t.destination_city}|${t.check_in_date.slice(0, 7)}`;
      if (weather[key] !== undefined) continue;
      setWeather((prev) => ({ ...prev, [key]: "loading" }));
      fetchWeather(t.destination_city, t.check_in_date, t.check_out_date)
        .then((w) => setWeather((prev) => ({ ...prev, [key]: w })))
        .catch(() => setWeather((prev) => ({ ...prev, [key]: null })));
    }
  }, [tracks]); // eslint-disable-line react-hooks/exhaustive-deps

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

  async function refresh() {
    try {
      setTracks(await listTracks());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // On load: if we have a saved token, validate it (fetch the user) then load
  // their tracks. A bad/expired token is cleared so the login screen shows.
  useEffect(() => {
    if (!getToken()) {
      setAuthReady(true);
      return;
    }
    me()
      .then((u) => {
        setUser(u);
        return refresh();
      })
      .catch(() => {
        clearToken();
        setUser(null);
      })
      .finally(() => setAuthReady(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await createTrack(url);
      setUrl("");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function onDelete(id: number) {
    await deleteTrack(id);
    await refresh();
  }

  async function onReset(id: number) {
    await resetTrack(id); // make the current price the new baseline
    await refresh();
  }

  function onLogout() {
    clearToken();
    setUser(null);
    setTracks([]);
  }

  async function toggleHistory(id: number) {
    setOpenHistory((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    if (!history[id]) {
      try {
        const detail = await getTrack(id);
        setHistory((prev) => ({ ...prev, [id]: detail.price_history || [] }));
      } catch {
        /* ignore — panel will show the empty state */
      }
    }
  }

  async function onCheckNow() {
    setError(null);
    setChecking(true);
    try {
      setTracks(await refreshTracks()); // re-fetches live prices for all my tracks
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

  // While we validate a saved token, render nothing (avoids a login-screen flash).
  if (!authReady) {
    return <div dir="rtl" className="wrap" aria-busy="true" />;
  }
  // Not signed in → show the login / register gate.
  if (!user) {
    return (
      <AuthScreen
        theme={theme}
        onTheme={setTheme}
        onAuthed={(token, u) => {
          setToken(token);
          setUser(u);
          refresh();
        }}
      />
    );
  }

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
          <div className="header-actions">
            <div className="account">
              <span className="account-email" title={user.email}>
                {user.email}
              </span>
              <button type="button" className="logout-btn" onClick={onLogout}>
                יציאה
              </button>
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
            gridTemplateColumns: "minmax(0,1fr) auto",
            alignItems: "end",
          }}
        >
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
        <div className="tickets-grid">
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
            const deal = gone ? null : dealBadge(t);
            const meta = hotelMeta(t.hotel_meta);
            const open = expanded === t.id;
            const photoList =
              meta?.photos?.length
                ? meta.photos
                : meta?.photo
                  ? [meta.photo]
                  : t.destination_photo_url
                    ? [t.destination_photo_url]
                    : [];

            return (
              <article
                key={t.id}
                className={`ticket reveal${gone ? " ticket--gone" : ""}${open ? " ticket--open" : ""}`}
                aria-expanded={open}
                onClick={(e) => {
                  // clicks on links/buttons act normally; elsewhere toggles the card
                  if ((e.target as HTMLElement).closest("a,button")) return;
                  setExpanded((cur) => (cur === t.id ? null : t.id));
                }}
                style={{
                  ["--spine" as string]: gone ? "var(--up)" : SPINE[t.status],
                  animationDelay: `${idx * 70}ms`,
                }}
              >
                <span className="expand-cue" aria-hidden>
                  {open ? "✕" : "⤢"}
                </span>
                {photoList.length > 0 && (
                  <PhotoCarousel photos={photoList} city={t.destination_city} stars={meta?.stars} />
                )}
                <div className="ticket-head">
                  <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                    <span className="badge">{PROVIDER_LABEL[t.provider] ?? t.provider}</span>
                    {gone ? (
                      <span className="status status--gone">⚠ לא זמינה יותר</span>
                    ) : (
                      <span className={`status ${st.cls}`}>{st.label}</span>
                    )}
                    {deal && <span className={`deal ${deal.cls}`}>{deal.label}</span>}
                  </div>
                  <div className="ticket-actions" style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                    <button className="history-btn" onClick={() => toggleHistory(t.id)} title="היסטוריית מחירים">
                      📈 היסטוריה
                    </button>
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

                {meta && (meta.review_score != null || meta.room || meta.board) && (
                  <div className="hotel-meta">
                    {meta.review_score != null && (
                      <span className="review-pill">
                        {meta.review_score} ★
                        {meta.review_count ? <small> · {meta.review_count.toLocaleString()} ביקורות</small> : null}
                      </span>
                    )}
                    {meta.room && <span className="meta-soft">🛏️ {meta.room}</span>}
                    {meta.board && <span className="meta-soft">🍽️ {meta.board}</span>}
                  </div>
                )}
                {meta?.highlight && <p className="hotel-highlight">{meta.highlight}</p>}

                <div className="stats">
                  {t.destination && <Stat icon="📍" label="יציאה" value={t.destination} />}
                  <Stat
                    icon="📅"
                    label="תאריכים"
                    value={`${dm(t.check_in_date)} – ${dm(t.check_out_date)}${n ? ` · ${n} ל׳` : ""}`}
                  />
                  {occ && <Stat icon="👥" label="תפוסה" value={occ} />}
                  {(() => {
                    const wk =
                      t.destination_city && t.check_in_date
                        ? `${t.destination_city}|${t.check_in_date.slice(0, 7)}`
                        : null;
                    const w = wk ? weather[wk] : undefined;
                    return w && w !== "loading" ? (
                      <Stat icon="☀️" label={t.destination_city ?? "מזג אוויר"} value={`~${w.tmax}°/${w.tmin}°`} />
                    ) : null;
                  })()}
                </div>

                {meta?.tags?.length ? (
                  <div className="tags">
                    {meta.tags.map((tg, i) => (
                      <span className="tag" key={i}>
                        {tg}
                      </span>
                    ))}
                  </div>
                ) : null}

                {meta && (meta.refundable_until || meta.flight_label || meta.luggage) && (
                  <div className="meta-row">
                    {meta.refundable_until && (
                      <span className="meta-chip meta-chip--ok">↩️ ביטול חינם עד {dm(meta.refundable_until)}</span>
                    )}
                    {meta.flight_label && (
                      <span className="meta-chip">
                        ✈️ {meta.flight_label}
                        {meta.airline ? ` · ${meta.airline}` : ""}
                        {meta.flight_kind === "charter" ? " · טיסת שכר" : ""}
                      </span>
                    )}
                    {luggageLabel(meta.luggage) && (
                      <span className="meta-chip">{luggageLabel(meta.luggage)}</span>
                    )}
                  </div>
                )}

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
                        <FlapPrice text={money(t.current_price, t.currency)} />
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
                    {(() => {
                      const pf = packageFlight(t.flight_details);
                      if (!pf) return null;
                      const line = (l: PkgLeg | null, icon: string, label: string) =>
                        l && l.dep ? (
                          <span>
                            {icon} {label} {l.date} · {l.airline ? `${l.airline} · ` : ""}
                            {l.dep}→{l.arr}
                            {l.stops === 0 ? " · ישיר" : l.stops ? ` · ${l.stops} עצירות` : ""}
                          </span>
                        ) : null;
                      return (
                        <div className="alt-flight">
                          {line(pf.out, "🛫", "הלוך")}
                          {line(pf.back, "🛬", "חזור")}
                        </div>
                      );
                    })()}
                    <div className="checked-at">🕐 {lastChecked(t.last_checked_at)}</div>
                  </div>

                  <div className="foot-links" style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                    {meta?.maps_url && (
                      <a className="offer-link offer-link--alt" href={meta.maps_url} target="_blank" rel="noreferrer">
                        📍 במפה
                        <span aria-hidden>↗</span>
                      </a>
                    )}
                    {t.hotel_url && (
                      <a className="offer-link offer-link--alt" href={t.hotel_url} target="_blank" rel="noreferrer">
                        🏨 מחיר באתר המלון
                        <span aria-hidden>↗</span>
                      </a>
                    )}
                    <a className="offer-link" href={t.raw_url} target="_blank" rel="noreferrer">
                      פתחו את ההצעה
                      <span aria-hidden>↗</span>
                    </a>
                  </div>
                </div>

                {t.alt_price && t.provider === "travelist" && (
                  <a className="alt-suggest" href={t.alt_url ?? t.raw_url} target="_blank" rel="noreferrer">
                    <div>
                      💡 טיסה מסחרית לתאריכים שלך: <b>{money(t.alt_price, t.currency)}</b>
                      {t.current_price && Number(t.current_price) > Number(t.alt_price) && (
                        <span className="alt-save">
                          {" "}
                          חיסכון {sym(t.currency)}
                          {Math.round(Number(t.current_price) - Number(t.alt_price)).toLocaleString()}
                        </span>
                      )}
                      <span aria-hidden> ↗</span>
                    </div>
                    {(() => {
                      const pf = packageFlight(t.alt_details);
                      if (!pf) return null;
                      const line = (l: PkgLeg | null, icon: string, label: string) =>
                        l && l.dep ? (
                          <span>
                            {icon} {label} {l.date} · {l.airline ? `${l.airline} · ` : ""}
                            {l.dep}→{l.arr}
                            {l.stops === 0 ? " · ישיר" : l.stops ? ` · ${l.stops} עצירות` : ""}
                          </span>
                        ) : null;
                      return (
                        <div className="alt-flight">
                          {line(pf.out, "🛫", "הלוך")}
                          {line(pf.back, "🛬", "חזור")}
                        </div>
                      );
                    })()}
                  </a>
                )}
                {t.alt_price && t.provider !== "travelist" && (
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

                {(() => {
                  const offers = compareOffers(t.compare_offers);
                  if (offers.length === 0) return null;
                  return (
                    <div className="compare">
                      <div className="compare-head">
                        ✈️ רדאר טיסות — הזול שנצפה לאחרונה למסלול
                        <span className="compare-note">מחירים מקושיים, לא בזמן אמת</span>
                      </div>
                      <div className="compare-list">
                        {offers.map((o, i) => {
                          const row = (
                            <>
                              {o.note && <span className="compare-date tnum">{o.note}</span>}
                              <span className="compare-agency">{o.agency}</span>
                              <span className="compare-price tnum">
                                {sym(o.currency)}
                                {Math.round(o.price).toLocaleString()}
                              </span>
                              {i === 0 && <span className="compare-best">הכי זול</span>}
                            </>
                          );
                          return o.url ? (
                            <a
                              key={i}
                              className={`compare-row${i === 0 ? " is-best" : ""}`}
                              href={o.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {row}
                              <span aria-hidden>↗</span>
                            </a>
                          ) : (
                            <div key={i} className={`compare-row${i === 0 ? " is-best" : ""}`}>
                              {row}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}

                {openHistory.has(t.id) &&
                  (() => {
                    const pts = history[t.id];
                    if (!pts) return <div className="history-panel history-empty">טוען…</div>;
                    if (pts.length < 2)
                      return <div className="history-panel history-empty">אין עדיין היסטוריית מחירים — נצברת בכל בדיקה.</div>;
                    const log = changeLog(pts);
                    return (
                      <div className="history-panel">
                        <Sparkline points={pts} />
                        <div className="history-list">
                          {log
                            .slice()
                            .reverse()
                            .map((e, i) => (
                              <div className="history-row" key={i}>
                                <div className="history-main">
                                  <span className="history-date">
                                    {(() => {
                                      const d = parseUTC(e.date);
                                      return `${ddmm(d)} · ${hhmm(d)}`;
                                    })()}
                                  </span>
                                  <span className="history-price tnum">{money(String(e.price), t.currency)}</span>
                                  {e.delta !== null && e.delta !== 0 && (
                                    <span className={`delta delta--${e.delta < 0 ? "down" : "up"} tnum`}>
                                      {e.delta < 0 ? "▼" : "▲"} {sym(t.currency)}
                                      {Math.abs(Math.round(e.delta)).toLocaleString()}
                                    </span>
                                  )}
                                </div>
                                {(e.hotel != null || e.flight != null) && (
                                  <div className="history-parts tnum">
                                    {e.hotel != null && <span>🏨 {money(String(e.hotel), t.currency)}</span>}
                                    {e.flight != null && <span>✈️ {money(String(e.flight), t.currency)}</span>}
                                  </div>
                                )}
                                {(!!e.hotelDelta || !!e.flightDelta) && (
                                  <div className="history-why tnum">
                                    {!!e.hotelDelta && <span>🏨 {signed(e.hotelDelta, t.currency)}</span>}
                                    {!!e.flightDelta && <span>✈️ {signed(e.flightDelta, t.currency)}</span>}
                                  </div>
                                )}
                              </div>
                            ))}
                        </div>
                      </div>
                    );
                  })()}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
