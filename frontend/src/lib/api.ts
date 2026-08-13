/**
 * API client.
 *
 * Everything goes through `request`, which is responsible for four things the rest of the app
 * then never has to think about:
 *
 * 1. **Credentials.** `credentials: "include"` sends the HttpOnly session cookie. The token is
 *    never read by JavaScript — it cannot be, which is the point.
 * 2. **CSRF.** Unsafe methods copy the readable `tl_csrf` cookie into `X-CSRF-Token`. This is the
 *    double-submit half the browser can participate in.
 * 3. **The error envelope.** A failed response is turned into an `ApiError` carrying the server's
 *    code, message and field errors, so a form can render them inline instead of showing a
 *    generic failure.
 * 4. **Never inventing data.** A failure throws. No component ever receives a fabricated default
 *    that would render as a real number.
 */

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const CSRF_COOKIE = "tl_csrf";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export interface FieldError {
  field: string;
  code: string;
  message: string;
}

export interface ErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | undefined;

  constructor(status: number, payload: ErrorPayload) {
    super(payload.message);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code;
    this.details = payload.details ?? {};
    this.requestId = payload.request_id;
  }

  /** Field-level errors, ready to attach to form inputs. */
  get fieldErrors(): FieldError[] {
    const fields = this.details.fields;
    return Array.isArray(fields) ? (fields as FieldError[]) : [];
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** 402 — the workspace's plan does not include this feature. */
  get isEntitlement(): boolean {
    return this.status === 402;
  }

  get requiredPlan(): string | undefined {
    const plan = this.details.required_plan;
    return typeof plan === "string" ? plan : undefined;
  }

  get retryAfterSeconds(): number | undefined {
    const value = this.details.retry_after_seconds;
    return typeof value === "number" ? value : undefined;
  }
}

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_next: boolean;
}

export interface DataResponse<T> {
  data: T;
}

export interface ListResponse<T> {
  data: T[];
  meta: PageMeta;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Query parameters. Arrays repeat the key, matching the API's list filters. */
  query?: QueryParams;
  signal?: AbortSignal;
  /** Send a FormData body (uploads); Content-Type is left to the browser for the boundary. */
  formData?: FormData;
  headers?: Record<string, string>;
}

export type QueryValue = string | number | boolean | null | undefined | (string | number)[];
export type QueryParams = Record<string, QueryValue>;

export function buildQuery(params: QueryParams | undefined): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      // Repeated keys, which is what FastAPI expects for a list query parameter.
      for (const item of value) search.append(key, String(item));
    } else {
      search.append(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

/** Raised when the network itself failed — distinct from a server error response. */
export class NetworkError extends Error {
  constructor(message = "Could not reach the server. Check your connection and try again.") {
    super(message);
    this.name = "NetworkError";
  }
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };

  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  if (UNSAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers["X-CSRF-Token"] = token;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}${buildQuery(options.query)}`, {
      method,
      headers,
      body,
      credentials: "include",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new NetworkError();
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const envelope =
      payload && typeof payload === "object" && "error" in payload
        ? ((payload as { error: ErrorPayload }).error ?? null)
        : null;
    throw new ApiError(
      response.status,
      envelope ?? {
        code: "unexpected_error",
        // Deliberately generic: the body was not our envelope, so it may be a proxy page.
        message: `The server returned an unexpected ${response.status} response.`,
      },
    );
  }

  return payload as T;
}

/** Unwraps `{ data: ... }` for endpoints that return a single resource. */
export async function getData<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await request<DataResponse<T>>(path, options);
  return response.data;
}

/** Returns items and pagination for collection endpoints. */
export async function getList<T>(
  path: string,
  options: RequestOptions = {},
): Promise<ListResponse<T>> {
  return request<ListResponse<T>>(path, options);
}

export const api = {
  get: <T>(path: string, query?: QueryParams) => getData<T>(path, { query }),
  list: <T>(path: string, query?: QueryParams) => getList<T>(path, { query }),
  post: <T>(path: string, body?: unknown) => getData<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body?: unknown) => getData<T>(path, { method: "PATCH", body }),
  put: <T>(path: string, body?: unknown) => getData<T>(path, { method: "PUT", body }),
  delete: <T>(path: string) => getData<T>(path, { method: "DELETE" }),
  /** For endpoints returning `{ message, data }` rather than a resource envelope. */
  action: <T>(path: string, body?: unknown, method = "POST") =>
    request<T>(path, { method, body }),
  upload: <T>(path: string, formData: FormData) => getData<T>(path, { method: "POST", formData }),
};
