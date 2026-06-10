import { useEffect, useState } from "react";
import { createTrack, deleteTrack, listTracks, type Track } from "./api";

const STATUS = {
  Active: { label: "במעקב", cls: "bg-blue-100 text-blue-700" },
  Triggered: { label: "המחיר ירד!", cls: "bg-green-100 text-green-700" },
  Expired: { label: "פג", cls: "bg-gray-200 text-gray-600" },
} as const;

const PROVIDER_LABEL: Record<string, string> = {
  holidayfinder: "HolidayFinder",
  travelist: "Travelist",
  booking: "Booking",
};

function money(value: string | null, currency: string) {
  if (value === null) return "—";
  const sym = currency === "USD" ? "$" : currency === "ILS" ? "₪" : "";
  return `${sym}${Number(value).toLocaleString()}`;
}

// "2026-09-15" -> "15.09"
function dm(iso: string | null) {
  if (!iso) return "?";
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
}

function nights(a: string | null, b: string | null) {
  if (!a || !b) return null;
  const ms = new Date(b).getTime() - new Date(a).getTime();
  return Math.round(ms / 86_400_000);
}

// "2-adults,1-children" -> { persons: 3, label: "2 מבוגרים · 1 ילד" }
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

function Detail({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-base leading-none">{icon}</span>
      <span className="text-slate-400">{label}:</span>
      <span className="font-medium text-slate-700">{value}</span>
    </div>
  );
}

export default function App() {
  const [email, setEmail] = useState("");
  const [url, setUrl] = useState("");
  const [tracks, setTracks] = useState<Track[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div dir="rtl" className="min-h-screen bg-slate-50 text-slate-800">
      <div className="mx-auto max-w-3xl px-4 py-10">
        <header className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight">✈️ TripStalker</h1>
          <p className="text-slate-500">הדבק לינק למלון או חבילת נופש — ואנחנו נעקוב אחרי המחיר.</p>
        </header>

        <form onSubmit={onSubmit} className="mb-8 grid gap-3 rounded-xl bg-white p-5 shadow-sm sm:grid-cols-[1fr_1.6fr_auto]">
          <input
            type="email"
            required
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-500"
          />
          <input
            type="url"
            required
            placeholder="הדבק כאן לינק (HolidayFinder / Booking / Travelist)"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-500"
          />
          <button
            type="submit"
            disabled={loading}
            className="rounded-lg bg-blue-600 px-5 py-2 font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "מוסיף…" : "עקוב אחרי המחיר"}
          </button>
        </form>

        {error && (
          <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        <h2 className="mb-3 text-lg font-semibold">המעקבים שלי ({tracks.length})</h2>

        {tracks.length === 0 && (
          <div className="rounded-xl bg-white px-4 py-10 text-center text-slate-400 shadow-sm">
            עדיין אין מעקבים. הדבק לינק למעלה כדי להתחיל.
          </div>
        )}

        <div className="space-y-4">
          {tracks.map((t) => {
            const { persons, label: occ } = occupancy(t.room_config);
            const nightsCount = nights(t.check_in_date, t.check_out_date);
            const perPax =
              t.current_price && persons > 1
                ? (Number(t.current_price) / persons).toLocaleString(undefined, { maximumFractionDigits: 0 })
                : null;
            const st = STATUS[t.status] ?? STATUS.Active;
            const sym = t.currency === "USD" ? "$" : t.currency === "ILS" ? "₪" : "";
            const dropped =
              t.current_price && t.initial_price && Number(t.current_price) < Number(t.initial_price);
            const dropAmount = dropped
              ? (Number(t.initial_price) - Number(t.current_price)).toLocaleString(undefined, { maximumFractionDigits: 0 })
              : null;
            const dropPct = dropped
              ? ((1 - Number(t.current_price) / Number(t.initial_price)) * 100).toFixed(1)
              : null;

            return (
              <div key={t.id} className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-slate-100">
                {/* header row */}
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
                        {PROVIDER_LABEL[t.provider] ?? t.provider}
                      </span>
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${st.cls}`}>
                        {st.label}
                      </span>
                    </div>
                    {t.hotel_name && (
                      <div className="mt-1.5 font-semibold text-slate-800">{t.hotel_name}</div>
                    )}
                  </div>
                  <button
                    onClick={() => onDelete(t.id)}
                    className="shrink-0 text-sm text-slate-400 transition hover:text-red-600"
                    title="מחק מעקב"
                  >
                    🗑 מחק
                  </button>
                </div>

                {/* search details — what the user searched for */}
                <div className="mb-4 flex flex-wrap gap-x-5 gap-y-2 text-sm">
                  {t.destination && <Detail icon="📍" label="יעד/שדה" value={t.destination} />}
                  <Detail
                    icon="📅"
                    label="תאריכים"
                    value={`${dm(t.check_in_date)} → ${dm(t.check_out_date)}${nightsCount ? ` (${nightsCount} לילות)` : ""}`}
                  />
                  {occ && <Detail icon="👥" label="תפוסה" value={occ} />}
                </div>

                {/* price + actions */}
                <div className="flex flex-wrap items-end justify-between gap-3 border-t border-slate-100 pt-3">
                  <div className="flex items-end gap-4">
                    <div>
                      <div className="text-2xl font-bold text-slate-900">
                        {money(t.current_price, t.currency)}
                        <span className="mr-1 text-sm font-normal text-slate-400"> סה״כ</span>
                      </div>
                      {perPax && (
                        <div className="text-xs text-slate-500">
                          {sym}{perPax} לאדם · נרשם ב-{money(t.initial_price, t.currency)}
                        </div>
                      )}
                    </div>
                    {dropAmount && (
                      <span className="rounded-md bg-green-50 px-2 py-1 text-sm font-semibold text-green-700">
                        ↓ ירד {sym}{dropAmount} ({dropPct}%)
                      </span>
                    )}
                  </div>
                  <a
                    href={t.raw_url}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-100"
                  >
                    🔗 פתח את ההצעה באתר
                  </a>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
