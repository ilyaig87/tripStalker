// Thin API client for the TripStalker backend.

export type TrackStatus = "Active" | "Triggered" | "Expired";

export interface Track {
  id: number;
  provider: string;
  raw_url: string;
  destination: string | null;
  check_in_date: string | null;
  check_out_date: string | null;
  room_config: string | null;
  target_hotel_id_or_name: string | null;
  hotel_name: string | null;
  initial_price: string | null;
  current_price: string | null;
  currency: string;
  status: TrackStatus;
  available: boolean;
  last_error: string | null;
  last_checked_at: string | null;
  created_at: string;
}

// In dev: empty -> Vite proxies /api to the backend (see vite.config.ts).
// In prod: set VITE_API_BASE (e.g. https://tripstalker-api.onrender.com) at build time.
const BASE = import.meta.env.VITE_API_BASE ?? "";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export function createTrack(email: string, url: string): Promise<Track> {
  return fetch(`${BASE}/api/track`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, url }),
  }).then((r) => handle<Track>(r));
}

export function listTracks(email: string): Promise<Track[]> {
  return fetch(`${BASE}/api/user/tracks?email=${encodeURIComponent(email)}`).then((r) =>
    handle<Track[]>(r)
  );
}

export function deleteTrack(id: number): Promise<void> {
  return fetch(`${BASE}/api/track/${id}`, { method: "DELETE" }).then((r) => handle<void>(r));
}

// Re-check all of a user's tracks right now; returns the freshly updated list.
export function refreshTracks(email: string): Promise<Track[]> {
  return fetch(`${BASE}/api/user/refresh?email=${encodeURIComponent(email)}`, {
    method: "POST",
  }).then((r) => handle<Track[]>(r));
}
