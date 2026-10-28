import type { Alternative, Meta, Trip, TripInput } from "./types";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  if (res.status === 401) window.dispatchEvent(new Event("auth-expired")); // сессия кончилась — на страницу входа
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || `Ошибка ${res.status}`);
  }
  return data as T;
}

export const api = {
  meta: () => request<Meta>("GET", "/api/meta"),
  createTrip: (input: Partial<TripInput>) => request<Trip>("POST", "/api/trips", input),
  getTrip: (id: string) => request<Trip>("GET", `/api/trips/${id}`),
  setDuration: (id: string, itemId: string, duration: number) =>
    request<Trip>("PATCH", `/api/trips/${id}/items/${itemId}`, { duration }),
  replace: (id: string, itemId: string, placeId: string) =>
    request<Trip>("PATCH", `/api/trips/${id}/items/${itemId}`, { replace_with: placeId }),
  putPlace: (id: string, itemId: string, url: string) =>
    request<Trip>("POST", `/api/trips/${id}/items/${itemId}/place`, { url }),
  attachDoc: (id: string, itemId: string, form: FormData) =>
    request<Trip>("POST", `/api/trips/${id}/items/${itemId}/doc`, form),
  detachDoc: (id: string, itemId: string) => request<Trip>("DELETE", `/api/trips/${id}/items/${itemId}/doc`),
  removeFile: (id: string, itemId: string, name: string) =>
    request<Trip>("DELETE", `/api/trips/${id}/items/${itemId}/doc/files/${name}`),
  deleteItem: (id: string, itemId: string) => request<Trip>("DELETE", `/api/trips/${id}/items/${itemId}`),
  suggest: (id: string, day: number, after: string | null) =>
    request<{ places: Alternative[]; origin: string | null }>(
      "GET",
      `/api/trips/${id}/days/${day}/suggest` + (after ? `?after=${encodeURIComponent(after)}` : ""),
    ),
  addItem: (id: string, day: number, body: { after?: string | null; place_id?: string; title?: string; duration?: number }) =>
    request<Trip>("POST", `/api/trips/${id}/days/${day}/items`, body),
};

export const fmtHM = (m: number) => {
  const mm = Math.round(m);
  return `${String(Math.floor(mm / 60) % 24).padStart(2, "0")}:${String(mm % 60).padStart(2, "0")}`;
};

export const fmtDur = (m: number) => {
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (!h) return `${mm} мин`;
  return mm ? `${h} ч ${mm} мин` : `${h} ч`;
};

export const rub = (n: number) => `${Math.round(n).toLocaleString("ru-RU")} ₽`;

export function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

/** Миниатюра из Википедии: свободные лицензии, без ключа. Кешируем в памяти; нет фото — null. */
const photoCache = new Map<string, Promise<{ src: string; page: string } | null>>();
export function wikiPhoto(title: string) {
  if (!photoCache.has(title)) {
    const url = `https://ru.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title.replace(/ /g, "_"))}`;
    photoCache.set(
      title,
      fetch(url)
        .then((r) => (r.ok ? r.json() : null))
        .then((j) =>
          j && j.thumbnail ? { src: j.thumbnail.source as string, page: j.content_urls?.desktop?.page as string } : null,
        )
        .catch(() => null),
    );
  }
  return photoCache.get(title)!;
}

export function dateLabel(iso: string) {
  const [, m, d] = iso.split("-").map(Number);
  const months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  return `${d} ${months[m - 1]}`;
}
