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
  hotel_url: string | null;
  destination_city: string | null;
  destination_photo_url: string | null;
  initial_price: string | null;
  current_price: string | null;
  price_low: string | null;
  price_high: string | null;
  currency: string;
  status: TrackStatus;
  available: boolean;
  last_error: string | null;
  last_checked_at: string | null;
  alt_price: string | null;
  alt_check_in: string | null;
  alt_check_out: string | null;
  alt_url: string | null;
  alt_details: string | null;
  hotel_portion: string | null;
  flight_portion: string | null;
  flight_details: string | null;
  hotel_meta: string | null;
  compare_offers: string | null;
  created_at: string;
}

export interface AuthUser {
  id: number;
  email: string;
  created_at: string;
}

export interface AuthResult {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

// In dev: empty -> Vite proxies /api to the backend (see vite.config.ts).
// In prod: set VITE_API_BASE (e.g. https://tripstalker-api.onrender.com) at build time.
const BASE = import.meta.env.VITE_API_BASE ?? "";

const TOKEN_KEY = "ts_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// Attach the bearer token (when present) to outgoing requests.
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

// ---- auth ----
export function register(email: string, password: string): Promise<AuthResult> {
  return fetch(`${BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }).then((r) => handle<AuthResult>(r));
}

export function login(email: string, password: string): Promise<AuthResult> {
  return fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }).then((r) => handle<AuthResult>(r));
}

export function me(): Promise<AuthUser> {
  return fetch(`${BASE}/api/auth/me`, { headers: authHeaders() }).then((r) =>
    handle<AuthUser>(r)
  );
}

// ---- tracks (all scoped to the authenticated user via the token) ----
export function createTrack(url: string): Promise<Track> {
  return fetch(`${BASE}/api/track`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ url }),
  }).then((r) => handle<Track>(r));
}

export function listTracks(): Promise<Track[]> {
  return fetch(`${BASE}/api/user/tracks`, { headers: authHeaders() }).then((r) =>
    handle<Track[]>(r)
  );
}

export function deleteTrack(id: number): Promise<void> {
  return fetch(`${BASE}/api/track/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  }).then((r) => handle<void>(r));
}

export interface PriceHistoryPoint {
  price: string;
  hotel_portion: string | null;
  flight_portion: string | null;
  checked_at: string;
}

// Full track detail including its recorded price history.
export function getTrack(id: number): Promise<Track & { price_history: PriceHistoryPoint[] }> {
  return fetch(`${BASE}/api/track/${id}`, { headers: authHeaders() }).then((r) =>
    handle<Track & { price_history: PriceHistoryPoint[] }>(r)
  );
}

// Reset a track's baseline to its current price (clears a false drop/increase).
export function resetTrack(id: number): Promise<Track> {
  return fetch(`${BASE}/api/track/${id}/reset`, {
    method: "POST",
    headers: authHeaders(),
  }).then((r) => handle<Track>(r));
}

// Re-check all of the user's tracks right now; returns the freshly updated list.
export function refreshTracks(): Promise<Track[]> {
  return fetch(`${BASE}/api/user/refresh`, {
    method: "POST",
    headers: authHeaders(),
  }).then((r) => handle<Track[]>(r));
}
