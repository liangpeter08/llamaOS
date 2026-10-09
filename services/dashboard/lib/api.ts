// Thin client for the dashboard API (same origin; Next.js proxies /api/* to Python).

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let csrfToken = "";
export function setCsrf(token: string) {
  csrfToken = token;
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
    location.href = "/login";
  }
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = typeof body === "object" && body && "detail" in body ? String(body.detail) : `HTTP ${res.status}`;
    throw new ApiError(res.status, msg);
  }
  return body as T;
}

export async function get<T = any>(path: string): Promise<T> {
  return handle<T>(await fetch(path, { credentials: "same-origin", cache: "no-store" }));
}

export async function post<T = any>(path: string, body: unknown = {}): Promise<T> {
  return handle<T>(
    await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
      body: JSON.stringify(body),
    }),
  );
}
