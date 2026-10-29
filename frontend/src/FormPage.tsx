import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { api, dateLabel, plural, rub } from "./api";
import { AddressInput } from "./AddressInput";
import { Logo, navigate, UserMenu } from "./nav";
import { AltList, OwnPhoto, Photo, StayImport } from "./Picks";
import type { Alternative, Meta, TripInput } from "./types";

const PACE_HINT: Record<string, string> = {
  max: "4 и больше мест в день, плотный график",
  balanced: "Сбалансированно: есть время на еду и отдых",
  calm: "Спокойно, с длинными прогулками",
  relaxed: "Минимум программы, максимум отдыха",
};

const iso = (d: Date) => d.toISOString().slice(0, 10);
function addDays(s: string, n: number) {
  const d = new Date(s + "T12:00:00");
  d.setDate(d.getDate() + n);
  return iso(d);
}
function daysBetween(a: string, b: string) {
  return Math.round((new Date(b + "T12:00:00").getTime() - new Date(a + "T12:00:00").getTime()) / 86400000) + 1;
}

type Booking = {
  kind: string;
  title: string;
  date: string;
  start: string;
  end: string;
  end_date: string;
  number: string;
  from_city: string;
  to_city: string;
  venue: string;
  address: string;
  price: number;
  persons: number;
  role: "out" | "back" | "stay" | "event";
  lat?: number;
  lon?: number;
  place_id?: string;
  /** загруженный файл: до создания поездки — во временной папке (token), после — в файлах поездки (trip) */
  file?: { token: string; name: string; filename: string; trip?: string } | null;
  source?: string;
};

/** Ссылка на файл билета: открыть в браузере или (download) скачать под исходным именем. */
function ticketFileUrl(f: NonNullable<Booking["file"]>, download = false) {
  const base = f.trip ? `/api/trips/${f.trip}/files/${f.name}` : `/api/bookings/files/${f.token}/${f.name}`;
  return `${base}?filename=${encodeURIComponent(f.filename)}${download ? "&download" : ""}`;
}

type ManualStay = {
  name: string;
  address: string;
  lat: number | null;
  lon: number | null;
  price_night: string;
  check_in: string;
  check_out: string;
  kind: "hotel" | "apartment";
  url: string;
};

const DEFAULTS = (() => {
  const start = addDays(iso(new Date()), 3);
  return {
    origin: "Москва",
    home_address: "",
    city: "Санкт-Петербург",
    start_date: start,
    end_date: addDays(start, 2),
    start_time: "09:00",
    return_time: "18:00",
    transport: "auto",
    return_transport: "auto",
    adults: 2,
    kids_ages: [] as number[],
    budget: "100000",
    pace: "balanced",
    stay: "any",
    stay_id: "", // выбранное жильё из каталога; пусто — подберём по бюджету
    stay_url: "", // «уже есть бронь»: ссылка на объявление Суточно.ру
    stay_manual: null as ManualStay | null, // бронь на другом сайте (Авито, Trip.com…), введённая вручную
    bookings: [] as Booking[], // загруженные билеты и брони: дорога, жильё, события с временем
    wishes: "Хотелось бы посетить театр недорого",
    tags: [] as string[],
    to_station_min: 40,
    boarding_min: "" as string | number,
    sight_buffer_min: 0,
    step_min: 5,
    min_rating: 4.5,
    budget_tolerance_pct: 10,
    arrive_time: "",
    return_arrive_time: "",
    out_number: "",
    back_number: "",
    out_price: 0,
    back_price: 0,
  };
})();
type FormState = typeof DEFAULTS;

/** В форме храним только улицу и дом: город берётся из «Города отправления». */
function stripCity(address: string, city: string) {
  const a = (address || "").trim();
  const prefix = `${city.trim()}, `;
  return city && a.toLowerCase().startsWith(prefix.toLowerCase()) ? a.slice(prefix.length) : a;
}

function fromTrip(i: TripInput): FormState {
  return {
    ...DEFAULTS,
    ...i,
    end_date: i.end_date || addDays(i.start_date, i.days - 1),
    return_transport: i.return_transport || i.transport,
    stay_id: i.stay_id || "",
    stay_url: i.stay_url || "",
    stay_manual: (i.stay_manual as ManualStay | null | undefined) ?? null,
    bookings: (i.bookings as Booking[] | undefined) ?? [],
    budget: String(i.budget),
    boarding_min: i.boarding_min ?? "",
    home_address: stripCity(i.home_address || "", i.origin),
  };
}

// Черновик формы: вернулись «Назад» со страницы поездки — введённое на месте
const DRAFT_KEY = "svoim-hodom:form-draft";

function loadDraft(): FormState | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    const d = { ...DEFAULTS, ...saved } as FormState;
    if (!saved.return_transport) d.return_transport = d.transport; // черновик до раздельного «туда / обратно»
    if (d.start_date < iso(new Date())) {
      // даты в прошлом — подставляем свежие, остальное оставляем
      const len = Math.max(1, daysBetween(d.start_date, d.end_date));
      d.start_date = DEFAULTS.start_date;
      d.end_date = addDays(d.start_date, len - 1);
    }
    return d;
  } catch {
    return null;
  }
}

function saveDraft(f: FormState) {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(f));
  } catch {
    /* хранилище недоступно — просто без черновика */
  }
}

export function FormPage() {
  const editId = new URLSearchParams(window.location.search).get("edit");
  const [meta, setMeta] = useState<Meta | null>(null);
  const fresh = new URLSearchParams(window.location.search).has("new");
  const [f, setF] = useState<FormState>(() => (editId || fresh ? DEFAULTS : loadDraft() || DEFAULTS));
  // при изменении вводных показываем форму, только когда поездка загружена:
  // иначе поля успевают «сброситься» на значения по умолчанию
  const [ready, setReady] = useState(!editId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [estimated, setEstimated] = useState("");
  // направления, которые только что заполнил билет: смена транспорта не должна стирать номер и время
  const fromTicket = useRef<Set<string>>(new Set());

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((s) => ({ ...s, [k]: v }));

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(e.message));
    if (editId)
      api
        .getTrip(editId)
        .then((t) => setF(fromTrip(t.input)))
        .catch((e) => setError(`Не удалось загрузить поездку: ${(e as Error).message}`))
        .finally(() => setReady(true));
  }, [editId]);

  useEffect(() => {
    if (ready) saveDraft(f);
  }, [f, ready]);

  // «⇄ Изменить жильё» в маршруте — сразу к разделу «Где жить»
  const focus = new URLSearchParams(window.location.search).get("focus");
  useEffect(() => {
    if (ready && focus) setTimeout(() => document.getElementById(focus)?.scrollIntoView({ behavior: "smooth" }), 100);
  }, [ready, focus]);

  const days = Math.max(0, daysBetween(f.start_date, f.end_date));
  const transportKey = f.transport === "auto" ? "train" : f.transport;
  const boardingDefault = meta?.boarding_default?.[transportKey] ?? 30;

  function setStart(v: string) {
    const len = Math.max(1, days);
    setF((s) => ({ ...s, start_date: v, end_date: addDays(v, len - 1) }));
  }
  function setLength(n: number) {
    set("end_date", addDays(f.start_date, Math.max(1, Math.min(7, n)) - 1));
  }
  const toggleTag = (k: string) => set("tags", f.tags.includes(k) ? f.tags.filter((x) => x !== k) : [...f.tags, k]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const trip = await api.createTrip({
        ...f,
        budget: Number(String(f.budget).replace(/\s/g, "")) || 0,
        home_address: f.home_address.trim() ? `${f.origin.trim()}, ${f.home_address.trim()}` : "",
        ...(f.transport === "auto" ? { arrive_time: "", out_number: "", out_price: 0 } : {}),
        ...(f.return_transport === "auto" ? { return_arrive_time: "", back_number: "", back_price: 0 } : {}),
        boarding_min: f.boarding_min === "" ? null : Number(f.boarding_min),
      } as unknown as Partial<TripInput>);
      navigate(`/trip/${trip.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!ready)
    return (
      <div className="page">
        <header className="top">
          <div className="wrap top-bar">
            <Logo />
          </div>
        </header>
        <main className="wrap">
          <p className="loading-line" role="status">
            <span className="spinner" aria-hidden="true" /> Загружаем вводные поездки…
          </p>
        </main>
      </div>
    );

  return (
    <div className="page">
      <header className="top">
        <div className="wrap top-bar">
          <Logo />
          <nav className="top-nav" aria-label="Основное меню">
            <UserMenu />
          </nav>
        </div>
        <div className="wrap hero">
          <div className="eyebrow">Планировщик самостоятельных поездок</div>
          <h1>{editId ? "Изменить вводные поездки" : "Поездка по России — по часам, с отелями, едой и билетами"}</h1>
          <p>
            {editId
              ? "Поправьте данные и постройте маршрут заново — прежний вариант сохранится по своей ссылке."
              : "Укажите, куда и с кем едете. Составим программу на каждый день: когда выходить из дома, где поесть, что посмотреть и где ночевать — со ссылками на всё."}
          </p>
        </div>
      </header>

      <main className="wrap form-layout">
        <form className="card form" onSubmit={submit}>
          <Section n={1} title="Откуда и куда">
            <div className="row">
              <Field label="Город отправления" id="origin">
                <input
                  id="origin"
                  value={f.origin}
                  onChange={(e) => {
                    const v = e.target.value;
                    // другой город — прежний адрес и оценка дороги больше не подходят
                    setF((st) => ({ ...st, origin: v, home_address: "" }));
                    setEstimated("");
                  }}
                  required
                />
              </Field>
              <Field label="Куда" id="city">
                <select id="city" value={f.city} onChange={(e) => set("city", e.target.value)}>
                  {(meta?.cities || ["Санкт-Петербург"]).map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="row align-top">
              <Field label="Откуда выходите: улица и дом" id="home" grow>
                <AddressInput
                  id="home"
                  value={f.home_address}
                  origin={f.origin}
                  city={f.city}
                  transport={f.transport}
                  onChange={(v) => set("home_address", v)}
                  onPick={(s) => {
                    setF((st) => ({ ...st, home_address: s.value, to_station_min: s.to_station_min || st.to_station_min }));
                    setEstimated(s.to_station_min ? `≈ ${dur(s.to_station_min)} (${s.station})` : "");
                  }}
                />
              </Field>
            </div>
            <p className="hint">
              {estimated
                ? `Дорога до места отправления ${estimated} — оценка по расстоянию. В маршруте её можно поправить кнопками − и +.`
                : "Выберите адрес из подсказок — оценим время дороги до вокзала или аэропорта и построим маршрут в Яндекс Картах."}
            </p>
          </Section>

          <Section n={2} title="Когда">
            <div className="row">
              <Field label="Дата выезда" id="date">
                <input id="date" type="date" value={f.start_date} onChange={(e) => setStart(e.target.value)} required />
              </Field>
              <Field label="Дата возвращения" id="edate">
                <input
                  id="edate"
                  type="date"
                  min={f.start_date}
                  max={addDays(f.start_date, 6)}
                  value={f.end_date}
                  onChange={(e) => set("end_date", e.target.value)}
                  required
                />
              </Field>
            </div>
            <div className="days-line">
              <span>Получается</span>
              <Stepper
                value={days > 0 ? `${days} ${plural(days, "день", "дня", "дней")}` : "—"}
                dec={() => setLength(days - 1)}
                inc={() => setLength(days + 1)}
                what="дней"
              />
              <span className="hint">до 7 дней в прототипе</span>
            </div>
          </Section>

          <Section n={3} title="Уже есть билеты или брони?">
            <BookingsUpload f={f} setF={setF} keep={fromTicket} />
          </Section>

          <Section n={4} title="На чём">
            <Schedule
              keep={fromTicket}
              f={f}
              set={set}
              transports={meta?.transports || []}
              pick={(tk, k) =>
                setF((st) =>
                  tk === "transport"
                    ? // обратно — как туда, пока там не выбрали другое
                      { ...st, transport: k, return_transport: st.return_transport === st.transport ? k : st.return_transport }
                    : { ...st, return_transport: k },
                )
              }
              driveMin={meta?.drive_minutes?.[f.city]?.[f.origin.trim().toLowerCase()]}
            />
          </Section>

          <Section n={5} title="С кем">
            <div className="row">
              <div className="counter">
                <div>
                  <b>Взрослые</b>
                  <span>от 12 лет</span>
                </div>
                <Stepper
                  value={String(f.adults)}
                  dec={() => set("adults", Math.max(1, f.adults - 1))}
                  inc={() => set("adults", Math.min(10, f.adults + 1))}
                  what="взрослых"
                />
              </div>
              <div className="counter">
                <div>
                  <b>Дети</b>
                  <span>до 12 лет</span>
                </div>
                <Stepper
                  value={String(f.kids_ages.length)}
                  dec={() => set("kids_ages", f.kids_ages.slice(0, -1))}
                  inc={() => f.kids_ages.length < 6 && set("kids_ages", [...f.kids_ages, 7])}
                  what="детей"
                />
              </div>
            </div>
            {f.kids_ages.length > 0 && (
              <div className="kids">
                <span>Возраст — подберём детские места и добавим перерывы:</span>
                {f.kids_ages.map((age, i) => (
                  <label key={i}>
                    Ребёнок {i + 1}
                    <select
                      value={age}
                      onChange={(e) => set("kids_ages", f.kids_ages.map((a, j) => (j === i ? Number(e.target.value) : a)))}
                    >
                      {Array.from({ length: 12 }, (_, a) => (
                        <option key={a} value={a}>
                          {a === 0 ? "до 1 года" : `${a} ${plural(a, "год", "года", "лет")}`}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            )}
          </Section>

          <Section n={6} title="Бюджет">
            <div className="row">
              <Field label="Сумма" id="budget" narrow>
                <div className="money">
                  <input id="budget" inputMode="numeric" value={f.budget} onChange={(e) => set("budget", e.target.value)} />
                  <span aria-hidden="true">₽</span>
                </div>
              </Field>
              <Field label="Можно превысить на" id="tol" narrow>
                <select id="tol" value={f.budget_tolerance_pct} onChange={(e) => set("budget_tolerance_pct", Number(e.target.value))}>
                  {[0, 5, 10, 15, 20].map((v) => (
                    <option key={v} value={v}>
                      {v === 0 ? "не превышать" : `до ${v}%`}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <p className="hint">На всех за всю поездку, включая дорогу и проживание.</p>
          </Section>

          <Section n={7} title="Где жить" id="stay">
            <StayPicker f={f} set={set} kinds={meta?.stays || []} days={days} />
          </Section>

          <Section n={8} title="Темп путешествия">
            <div className="paces" role="radiogroup" aria-label="Темп">
              {(meta?.paces || []).map((p) => (
                <button
                  type="button"
                  role="radio"
                  aria-checked={f.pace === p.key}
                  key={p.key}
                  className={`pace ${f.pace === p.key ? "on" : ""}`}
                  onClick={() => set("pace", p.key)}
                >
                  <span className="dot" />
                  <span>
                    <b>{p.label}</b>
                    <small>{PACE_HINT[p.key]}</small>
                  </span>
                </button>
              ))}
            </div>
          </Section>

          <Section n={9} title="Что интересно и особые пожелания">
            {!!meta?.interests?.length && (
              <p className="hint">Что вам интересно — такие места поставим в программу первыми</p>
            )}
            {!!meta?.interests?.length && (
              <div className="chips" role="group" aria-label="Интересы">
                {meta.interests.map((w) => (
                  <button
                    type="button"
                    key={w.key}
                    className={`chip ${f.tags.includes(w.key) ? "on" : ""}`}
                    aria-pressed={f.tags.includes(w.key)}
                    onClick={() => toggleTag(w.key)}
                  >
                    {w.label}
                  </button>
                ))}
              </div>
            )}
            <label htmlFor="wishes" className="sr">
              Особые пожелания
            </label>
            <textarea id="wishes" rows={3} value={f.wishes} onChange={(e) => set("wishes", e.target.value)} />
            <div className="chips">
              {(meta?.wishes || []).map((w) => (
                <button
                  type="button"
                  key={w.key}
                  className={`chip ${f.tags.includes(w.key) ? "on" : ""}`}
                  aria-pressed={f.tags.includes(w.key)}
                  onClick={() => toggleTag(w.key)}
                >
                  {w.label}
                </button>
              ))}
            </div>
          </Section>

          <section className="sec settings">
            <button
              type="button"
              className="settings-toggle"
              aria-expanded={showSettings}
              onClick={() => setShowSettings(!showSettings)}
            >
              <span>⚙ Настройки поездки</span>
              <small>запасы времени, шаг, рейтинг</small>
              <b aria-hidden="true">{showSettings ? "−" : "+"}</b>
            </button>
            {showSettings && (
              <div className="settings-body">
                <div className="row">
                  <Field label="Запас на посадку, мин" id="boarding">
                    <input
                      id="boarding"
                      type="number"
                      min={0}
                      max={240}
                      placeholder={`по умолчанию ${boardingDefault}`}
                      value={f.boarding_min}
                      onChange={(e) => set("boarding_min", e.target.value === "" ? "" : Number(e.target.value))}
                    />
                  </Field>
                  <Field label="Запас к каждой достопримечательности, мин" id="buffer">
                    <input
                      id="buffer"
                      type="number"
                      min={0}
                      max={120}
                      step={5}
                      value={f.sight_buffer_min}
                      onChange={(e) => set("sight_buffer_min", Number(e.target.value))}
                    />
                  </Field>
                </div>
                <div className="row">
                  <Field label="Шаг кнопок − и + в маршруте" id="step">
                    <select id="step" value={f.step_min} onChange={(e) => set("step_min", Number(e.target.value))}>
                      {[5, 10, 15].map((v) => (
                        <option key={v} value={v}>
                          {v} минут
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Рейтинг мест не ниже" id="rating">
                    <select id="rating" value={f.min_rating} onChange={(e) => set("min_rating", Number(e.target.value))}>
                      {Array.from({ length: 21 }, (_, i) => Math.round((3 + i / 10) * 10) / 10).map((v) => (
                        <option key={v} value={v}>
                          {v.toFixed(1).replace(".", ",")}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <p className="hint">
                  Посадка по умолчанию: поезд 30 мин, самолёт 2 ч, автобус 20 мин. Фильтр по рейтингу начнёт работать,
                  когда подключим данные Яндекс Карт — в черновом каталоге прототипа рейтингов нет.
                </p>
              </div>
            )}
          </section>

          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <div className="form-foot">
            <button className="primary big" type="submit" disabled={busy || days < 1}>
              {busy ? "Строим маршрут…" : editId ? "Построить заново" : "Построить маршрут"}
            </button>
            {!editId && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => {
                  if (!window.confirm("Очистить форму? Всё введённое вернётся к значениям по умолчанию.")) return;
                  setF(DEFAULTS);
                  setEstimated("");
                  setError("");
                }}
              >
                Очистить форму
              </button>
            )}
          </div>
        </form>

        <aside className="side">
          <div className="card dark">
            <h2>Что вы получите</h2>
            <ul className="ticks">
              <li>Программу на каждый день по часам — от выхода из дома до вечерней прогулки</li>
              <li>Отели, рестораны, музеи и театры со ссылками и фото</li>
              <li>Порядок мест с учётом расстояний, часов работы и темпа</li>
              <li>Замену, удаление и добавление любого пункта</li>
              <li>Билеты и брони в одном месте, проверку дат и точный бюджет</li>
            </ul>
          </div>
        </aside>
      </main>
    </div>
  );
}

type StayList = { nights: number; recommended: string; stays: (Alternative & { kind: string })[]; search: { label: string; url: string; ad?: string | null }[] };

/** Где жить — как «⇄ Заменить» у жилья в программе дня: выбранный вариант, а по кнопке — своя квартира
 * по ссылке Суточно.ру и варианты с ценой на даты и группу. */
function StayPicker({ f, set, kinds, days }: { f: FormState; set: SetF; kinds: { key: string; label: string }[]; days: number }) {
  const [data, setData] = useState<StayList | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Alternative | null>(null);
  const [error, setError] = useState("");
  const kidsKey = f.kids_ages.join(",");
  const budget = Number(String(f.budget).replace(/\s/g, "")) || 0;
  useEffect(() => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.start_date) || days < 2) return;
    let alive = true;
    const t = setTimeout(() => {
      // всё, от чего зависит подбор жилья в программе: бюджет за вычетом билетов, «подешевле» в пожеланиях
      const q = new URLSearchParams({
        city: f.city, origin: f.origin, start_date: f.start_date, end_date: f.end_date,
        adults: String(f.adults), kids: kidsKey, budget: String(budget), stay: f.stay,
        wishes: f.wishes, tags: f.tags.join(","), transport: f.transport, return_transport: f.return_transport,
        out_price: String(f.out_price || 0), back_price: String(f.back_price || 0),
      });
      fetch(`/api/stays?${q}`)
        .then((r) => (r.ok ? (r.json() as Promise<StayList>) : Promise.reject()))
        .then((d) => alive && (setData(d), setFailed(false)))
        .catch(() => alive && setFailed(true));
    }, 300);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [f.city, f.origin, f.start_date, f.end_date, f.adults, kidsKey, budget, f.stay, days, f.wishes, f.tags.join(","),
      f.transport, f.return_transport, f.out_price, f.back_price]);

  // своя бронь из сохранённых вводных или черновика — показываем объявление заново
  useEffect(() => {
    if (!f.stay_url || preview) return;
    let alive = true;
    fetch("/api/stays/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: f.stay_url, city: f.city }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => alive && j && setPreview(j as Alternative))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.stay_url]);

  const rows = (data?.stays || []).filter((s) => f.stay === "any" || s.kind === f.stay);
  // сменили город или вид жилья — выбранный вариант больше не подходит
  useEffect(() => {
    if (data && f.stay_id && !rows.some((s) => s.id === f.stay_id)) set("stay_id", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, f.stay]);

  if (days < 2) return <p className="hint">Поездка на один день — ночевать не нужно.</p>;
  const m = f.stay_manual;
  const auto = !f.stay_id && !f.stay_url && !m;
  const picked = m
    ? {
        id: "manual",
        name: m.name,
        meta: [
          m.kind === "apartment" ? "Квартира" : "Отель",
          m.price_night ? `≈ ${rub(Number(String(m.price_night).replace(/\s/g, "")) || 0)} за ночь` : "",
          m.check_in || m.check_out ? `заезд с ${m.check_in || "—"}, выезд до ${m.check_out || "—"}` : "",
          m.address,
        ]
          .filter(Boolean)
          .join(" · "),
      }
    : f.stay_url
      ? preview ?? { id: "url", name: "Квартира по ссылке", meta: f.stay_url }
      : rows.find((s) => s.id === f.stay_id) ?? data?.stays.find((s) => s.id === data.recommended);
  const reset = () => {
    set("stay_id", "");
    set("stay_url", "");
    set("stay_manual", null);
    setPreview(null);
  };
  return (
    <div className="stay-picker">
      <div className="chips" role="group" aria-label="Вид жилья">
        {kinds.map((k) => (
          <button
            type="button"
            key={k.key}
            className={`seg ${f.stay === k.key ? "on" : ""}`}
            aria-pressed={f.stay === k.key}
            onClick={() => set("stay", k.key)}
          >
            {k.label}
          </button>
        ))}
      </div>
      {failed && <p className="hint warn">Не удалось загрузить варианты жилья — подберём сами по бюджету.</p>}
      {!data && !failed && (
        <p className="loading-line" role="status">
          <span className="spinner" aria-hidden="true" /> Подбираем жильё на ваши даты…
        </p>
      )}
      {data && picked && (
        <div className="alt stay-chosen">
          {picked.photo ? <OwnPhoto src={picked.photo} alt={picked.name} /> : picked.wiki && <Photo title={picked.wiki} alt={picked.name} area={picked.wiki_area} />}
          <div>
            <small className="stay-tag">{f.stay_url || m ? "Своя бронь" : auto ? "Подберём сами по бюджету" : "Ваш выбор"}</small>
            <b>{picked.name}</b>
            <span>{picked.meta}</span>
          </div>
          <button type="button" className={`btn ${open ? "on" : ""}`} aria-expanded={open} onClick={() => setOpen(!open)}>
            ⇄ Заменить
          </button>
        </div>
      )}
      {data && open && (
        <div className="alts">
          <StayImport
            search={data.search}
            onSubmit={async (url) => {
              setError("");
              const r = await fetch("/api/stays/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, city: f.city }),
              }).catch(() => null);
              const j = r ? await r.json().catch(() => ({})) : {};
              if (!r?.ok) {
                setError(j.error || "Не удалось открыть объявление — проверьте ссылку");
                return false;
              }
              setPreview(j as Alternative);
              setF_url(url);
              setOpen(false);
              return true;
            }}
          />
          {error && <p className="hint warn">{error}</p>}
          <ManualStayForm
            city={f.city}
            initial={m}
            onSave={(v) => {
              set("stay_id", "");
              set("stay_url", "");
              setPreview(null);
              set("stay_manual", v);
              setOpen(false);
            }}
          />
          <div className="alts-title">Варианты на ваши даты</div>
          {!auto && (
            <button type="button" className="linkish stay-auto" onClick={reset}>
              Подобрать самим по бюджету
            </button>
          )}
          <AltList
            items={rows}
            page={4}
            placeholder="Поиск по названию, например «Баумана» или «квартира»"
            onPick={(a) => {
              set("stay_url", "");
              set("stay_manual", null);
              setPreview(null);
              set("stay_id", a.id);
              setOpen(false);
            }}
            end={<p className="hint">Цены — ориентир для бюджета. Жильё можно поменять и в маршруте кнопкой «Заменить».</p>}
          />
        </div>
      )}
    </div>
  );

  function setF_url(url: string) {
    set("stay_id", "");
    set("stay_manual", null);
    set("stay_url", url);
  }
}

/** Бронь на другом сайте (Авито, Trip.com, Островок, сайт отеля): данные с сайта не читаем — вводит человек. */
function ManualStayForm({ city, initial, onSave }: { city: string; initial: ManualStay | null; onSave: (m: ManualStay) => void }) {
  const blank: ManualStay = { name: "", address: "", lat: null, lon: null, price_night: "", check_in: "", check_out: "", kind: "apartment", url: "" };
  const [show, setShow] = useState(!!initial);
  const [m, setM] = useState<ManualStay>(initial ?? blank);
  const [error, setError] = useState("");
  const put = <K extends keyof ManualStay>(k: K, v: ManualStay[K]) => setM((s) => ({ ...s, [k]: v }));
  if (!show)
    return (
      <button type="button" className="linkish stay-auto" onClick={() => setShow(true)}>
        Бронь на другом сайте (Авито, Trip.com, Островок…) — ввести вручную
      </button>
    );
  return (
    <fieldset className="leg manual-stay">
      <legend>Своя бронь на другом сайте</legend>
      <div className="chips" role="group" aria-label="Вид своего жилья">
        {(["apartment", "hotel"] as const).map((k) => (
          <button type="button" key={k} className={`seg ${m.kind === k ? "on" : ""}`} aria-pressed={m.kind === k} onClick={() => put("kind", k)}>
            {k === "apartment" ? "Квартира" : "Отель"}
          </button>
        ))}
      </div>
      <div className="row">
        <label className="field grow">
          <span className="label">Название</span>
          <input value={m.name} placeholder="Например: Квартира у Кремля" onChange={(e) => put("name", e.target.value)} />
        </label>
        <label className="field grow">
          <span className="label">Ссылка на бронь или объявление</span>
          <input value={m.url} placeholder="https://www.avito.ru/…" onChange={(e) => put("url", e.target.value)} />
        </label>
      </div>
      <div className="row align-top">
        <div className="field grow">
          <label className="label" htmlFor="manual-stay-address">
            Адрес — выберите из подсказок
          </label>
          <AddressInput
            id="manual-stay-address"
            value={m.address}
            origin={city}
            city={city}
            transport="auto"
            onChange={(v) => setM((s) => ({ ...s, address: v, lat: null, lon: null }))}
            onPick={(s) => setM((st) => ({ ...st, address: s.value, lat: s.lat, lon: s.lon }))}
          />
        </div>
      </div>
      <div className="row">
        <label className="field">
          <span className="label">Цена за ночь, ₽</span>
          <input inputMode="numeric" value={m.price_night} onChange={(e) => put("price_night", e.target.value)} />
        </label>
        <label className="field">
          <span className="label">Заезд с</span>
          <input type="time" value={m.check_in} onChange={(e) => put("check_in", e.target.value)} />
        </label>
        <label className="field">
          <span className="label">Выезд до</span>
          <input type="time" value={m.check_out} onChange={(e) => put("check_out", e.target.value)} />
        </label>
      </div>
      {error && <p className="hint warn">{error}</p>}
      <div className="row">
        <button
          type="button"
          className="primary"
          onClick={() => {
            if (!m.name.trim()) return setError("Напишите, как называется жильё");
            if (m.lat == null) return setError("Выберите адрес из подсказок — по нему построим дорогу до мест программы");
            setError("");
            onSave({ ...m, address: m.address.trim(), name: m.name.trim() });
          }}
        >
          Жить здесь
        </button>
      </div>
      <p className="hint">Время заезда и выезда возьмите из брони — без него поставим обычное и напомним уточнить.</p>
    </fieldset>
  );
}

const BOOKING_KIND: Record<string, string> = {
  train: "Поезд", plane: "Самолёт", bus: "Автобус", theatre: "Театр", museum: "Музей", excursion: "Экскурсия", hotel: "Жильё", other: "Событие",
};
const BOOKING_ROLE: Record<string, string> = { out: "дорога туда", back: "дорога обратно", stay: "жильё", event: "в программу по времени" };

/** Билеты и брони в форме: PDF или текст письма -> распознанные брони; дорога заполняет «Туда» и «Обратно»,
 * жильё — «Где жить», события с временем жёстко встают в программу (сервер строит день вокруг них). */
function BookingsUpload({ f, setF, keep }: { f: FormState; setF: (u: (s: FormState) => FormState) => void; keep: KeepRef }) {
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);

  async function upload(files: File[], txt: string) {
    if (!files.length && !txt.trim()) return;
    setBusy(true);
    setErrors([]);
    setNotes([]);
    const fd = new FormData();
    files.forEach((file) => fd.append("file", file));
    if (txt.trim()) fd.append("text", txt);
    fd.append("origin", f.origin);
    fd.append("city", f.city);
    try {
      const r = await fetch("/api/bookings", { method: "POST", body: fd });
      const j = (await r.json()) as { bookings?: Booking[]; errors?: string[]; error?: string };
      if (!r.ok) throw new Error(j.error || "Не удалось распознать");
      setErrors(j.errors || []);
      const got = j.bookings || [];
      if (got.length) {
        const applied: string[] = [];
        setF((st) => applyBookings(st, got, applied, keep));
        setNotes(applied);
        setText("");
      }
    } catch (e) {
      setErrors([(e as Error).message]);
    } finally {
      setBusy(false);
    }
  }

  const remove = (i: number) => setF((st) => ({ ...st, bookings: st.bookings.filter((_, k) => k !== i) }));
  const edit = (i: number, k: keyof Booking, v: string) =>
    setF((st) => ({ ...st, bookings: st.bookings.map((b, n) => (n === i ? { ...b, [k]: v } : b)) }));

  return (
    <div className="bookings">
      <label
        className={`dropzone ${drag ? "on" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          upload(Array.from(e.dataTransfer.files), "");
        }}
      >
        <input
          type="file"
          multiple
          accept=".pdf,.txt,.eml,.html,.png,.jpg,.jpeg,.webp,.heic"
          aria-label="Файлы билетов и броней"
          onChange={(e) => {
            upload(Array.from(e.target.files || []), "");
            e.target.value = "";
          }}
        />
        <b>{busy ? "Распознаём билеты…" : "Перетащите сюда билеты и брони — PDF, фото или скриншот — или нажмите, чтобы выбрать"}</b>
        <span>Поезд и самолёт, театр, музей, экскурсия, отель — поставим в программу по времени из билета</span>
      </label>
      <div className="row align-top">
        <label className="field grow">
          <span className="label">Или вставьте текст письма с билетом</span>
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      </div>
      <button type="button" className="btn" disabled={busy || !text.trim()} onClick={() => upload([], text)}>
        Распознать текст
      </button>
      {busy && (
        <p className="loading-line" role="status">
          <span className="spinner" aria-hidden="true" /> Читаем билеты — это занимает до 10 секунд на документ
        </p>
      )}
      {errors.map((e) => (
        <p key={e} className="hint warn">
          {e}
        </p>
      ))}
      {notes.length > 0 && <p className="hint ok">✓ {notes.join(" · ")}</p>}
      {f.bookings.length > 0 && (
        <ul className="booking-list" aria-label="Загруженные билеты и брони">
          {f.bookings.map((b, i) => (
            <li key={i}>
              <div>
                <b>
                  {b.title.toLowerCase().startsWith((BOOKING_KIND[b.kind] || "").toLowerCase()) ? "" : `${BOOKING_KIND[b.kind] || "Бронь"} · `}
                  {b.title}
                </b>
                <small>
                  ✓ оплачено · {BOOKING_ROLE[b.role]}
                  {b.price ? ` · ${rub(b.price)}` : ""}
                  {!b.file && b.source ? ` · ${b.source}` : ""}
                  {b.file && (
                    <>
                      {" · "}
                      <a href={ticketFileUrl(b.file)} target="_blank" rel="noreferrer">
                        {b.file.filename} ↗
                      </a>
                    </>
                  )}
                </small>
              </div>
              {b.role === "event" ? (
                <span className="booking-time">
                  <input type="date" aria-label="Дата" value={b.date} onChange={(e) => edit(i, "date", e.target.value)} />
                  <input type="time" aria-label="Начало" value={b.start} onChange={(e) => edit(i, "start", e.target.value)} />
                  <input type="time" aria-label="Окончание" value={b.end} onChange={(e) => edit(i, "end", e.target.value)} />
                </span>
              ) : (
                <span className="booking-time">
                  {b.date.split("-").reverse().join(".")} {b.start}
                  {b.end ? `–${b.end}` : ""}
                </span>
              )}
              <button type="button" className="btn" aria-label={`Убрать «${b.title}»`} onClick={() => remove(i)}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="hint">
        Билеты в театр, музей и на экскурсию встанут в программу на своё время, остальное разложим вокруг них. Файлы
        приложим к пунктам маршрута. Фото и скриншоты — в JPG или PNG, у сканов PDF читаем первую страницу.
      </p>
    </div>
  );
}

/** Распознанные брони -> поля формы. applied — что поменяли, для подсказки под загрузкой. */
function applyBookings(st: FormState, got: Booking[], applied: string[], keep: KeepRef): FormState {
  const s: FormState = { ...st, bookings: [...st.bookings, ...got] };
  const perPerson = (b: Booking) => (b.price ? Math.round(b.price / Math.max(1, b.persons || s.adults + s.kids_ages.length)) : 0);
  const len = Math.max(1, daysBetween(s.start_date, s.end_date));
  for (const b of got) {
    if (b.role === "out" && ["train", "plane", "bus"].includes(b.kind)) {
      if (s.transport !== b.kind) keep.current.add("transport");
      if (s.return_transport === s.transport && s.return_transport !== b.kind) keep.current.add("return_transport");
      if (s.return_transport === s.transport) s.return_transport = b.kind;
      Object.assign(s, { transport: b.kind, start_time: b.start || s.start_time, arrive_time: b.end, out_number: b.number, out_price: perPerson(b) });
      if (b.date && b.date !== s.start_date) {
        s.start_date = b.date;
        s.end_date = addDays(b.date, len - 1);
        applied.push(`дата выезда — ${dateLabel(b.date)}`);
      }
      applied.push(`${b.title} — в «Туда»`);
    } else if (b.role === "back" && ["train", "plane", "bus"].includes(b.kind)) {
      if (s.return_transport !== b.kind) keep.current.add("return_transport");
      Object.assign(s, { return_transport: b.kind, return_time: b.start || s.return_time, return_arrive_time: b.end, back_number: b.number, back_price: perPerson(b) });
      if (b.date && b.date !== s.end_date && b.date >= s.start_date) {
        s.end_date = b.date;
        applied.push(`дата возвращения — ${dateLabel(b.date)}`);
      }
      applied.push(`${b.title} — в «Обратно»`);
    } else if (b.role === "stay" && b.lat != null && b.lon != null) {
      s.stay_id = "";
      s.stay_url = "";
      s.stay_manual = {
        name: b.venue || b.title, address: b.address, lat: b.lat, lon: b.lon,
        price_night: b.price ? String(Math.round(b.price / Math.max(1, daysBetween(b.date, b.end_date || b.date) - 1))) : "",
        check_in: b.start, check_out: b.end, kind: /квартир|апартамент/i.test(b.title + b.venue) ? "apartment" : "hotel", url: "",
      };
      applied.push(`${b.title} — в «Где жить»`);
    } else if (b.role === "event") {
      applied.push(`${b.title} — ${dateLabel(b.date)} в ${b.start || "?"} в программу`);
    }
  }
  return s;
}

const LEG_WORDS: Record<string, { number: string; dep: string; ph: string; arr?: string }> = {
  auto: { number: "", dep: "Желаемое время отправления", ph: "" },
  train: { number: "№ поезда", dep: "Отправление поезда", ph: "например 752А" },
  plane: { number: "№ рейса", dep: "Вылет", ph: "например SU 6" },
  bus: { number: "№ рейса", dep: "Отправление автобуса", ph: "необязательно" },
  car: { number: "", dep: "Выезд", ph: "", arr: "Приезд" },
};

const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
/** «27 октября, вт» — дата отправления в заголовке «Туда» и «Обратно». */
function legDate(iso: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return "дата не выбрана";
  const [y, m, d] = iso.split("-").map(Number);
  return `${dateLabel(iso)}, ${WEEKDAYS[new Date(y, m - 1, d).getDay()]}`;
}

type SetF = <K extends keyof FormState>(k: K, v: FormState[K]) => void;
type TransportKey = "transport" | "return_transport";
type Leg = { title: string; tk: TransportKey; from: string; to: string; dep: "start_time" | "return_time"; arr: "arrive_time" | "return_arrive_time"; num: "out_number" | "back_number"; price: "out_price" | "back_price"; date: string };
type Pick = (tk: TransportKey, k: string) => void;
type KeepRef = { current: Set<string> };

function Schedule({ f, set, transports, pick, driveMin, keep }: { f: FormState; set: SetF; transports: { key: string; label: string }[]; pick: Pick; driveMin?: number; keep: KeepRef }) {
  const auto = f.transport === "auto" || f.return_transport === "auto";
  // заранее подгружаем расписания всех видов транспорта на обе даты
  useEffect(() => {
    const ready = [f.start_date, f.end_date].every((d) => /^\d{4}-\d{2}-\d{2}$/.test(d));
    if (!f.origin || !f.city || !ready) return;
    const t = setTimeout(() => {
      for (const k of KINDS) {
        loadSchedule(f.origin, f.city, f.start_date, k);
        loadSchedule(f.city, f.origin, f.end_date, k);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [f.origin, f.city, f.start_date, f.end_date]);
  // выбрали поезд, самолёт или автобус — сразу открываем расписание этого направления
  const [openLeg, setOpenLeg] = useState<string | null>(null);
  const legs: Leg[] = [
    { title: `Туда · ${legDate(f.start_date)} · ${f.origin} → ${f.city}`, tk: "transport", from: f.origin, to: f.city, dep: "start_time", arr: "arrive_time", num: "out_number", price: "out_price", date: f.start_date },
    { title: `Обратно · ${legDate(f.end_date)} · ${f.city} → ${f.origin}`, tk: "return_transport", from: f.city, to: f.origin, dep: "return_time", arr: "return_arrive_time", num: "back_number", price: "back_price", date: f.end_date },
  ];
  return (
    <div className="schedule">
      {auto && (
        <p className="hint">
          Транспорт подберём сами — укажите, во сколько удобно выехать. Если билеты уже есть, выберите поезд, самолёт
          или автобус выше и впишите точное расписание.
        </p>
      )}
      {legs.map((l) => (
        <LegFields
          key={l.dep}
          leg={l}
          f={f}
          set={set}
          driveMin={driveMin}
          transports={transports}
          pick={pick}
          keep={keep}
          open={openLeg === l.dep}
          setOpen={(v) => setOpenLeg(v ? l.dep : null)}
          onPicked={() => setOpenLeg(l.dep === "start_time" && !f.back_number ? "return_time" : null)}
        />
      ))}
      {(f.transport !== "auto" && f.transport !== "car") || (f.return_transport !== "auto" && f.return_transport !== "car") ? (
        <p className="hint">
          Если прибытие не указать, время в пути оценим сами. С расписанием маршрут первого и последнего дня будет точным.
        </p>
      ) : null}
    </div>
  );
}

type Trip = { number: string; title: string; carrier: string; departure: string; arrival: string; next_day: boolean; duration_min: number; from: string; to: string; price_from: number | null; classes?: { name: string; price: number }[]; price_source?: string; cheapest?: boolean };
type Sched = { available: boolean; items: Trip[]; message?: string; error?: boolean; buy_links?: { label: string; url: string; ad?: string | null }[]; price_note?: string | null };

const normNum = (n: string) =>
  n.toUpperCase().replace(/[ABCEHKMOPTXY]/g, (c) => "АВСЕНКМОРТХУ"["ABCEHKMOPTXY".indexOf(c)]).replace(/[^0-9A-ZА-ЯЁ]/g, "");

// Расписания кэшируем в браузере и подгружаем заранее для всех видов транспорта,
// чтобы при выборе поезда, самолёта или автобуса список был готов сразу.
const schedCache = new Map<string, Promise<Sched>>();
const schedReady = new Map<string, Sched>();
const KINDS = ["train", "plane", "bus"];
const schedKey = (from: string, to: string, date: string, kind: string) => [from, to, date, kind].join("|");

function loadSchedule(from: string, to: string, date: string, kind: string): Promise<Sched> {
  const key = schedKey(from, to, date, kind);
  let p = schedCache.get(key);
  if (!p) {
    p = fetch(`/api/schedule?${new URLSearchParams({ from, to, date, transport: kind })}`)
      .then((r) => r.json() as Promise<Sched>)
      .catch(() => {
        return { available: false, error: true, items: [], message: "Нет связи с сервером приложения" } as Sched;
      })
      .then((j) => {
        schedReady.set(key, j);
        if (j.error) schedCache.delete(key); // ошибку не запоминаем — «Повторить» спросит снова
        return j;
      });
    schedCache.set(key, p);
  }
  return p;
}

function useSchedule(from: string, to: string, date: string, kind: string): [Sched | null, () => void] {
  const ok = KINDS.includes(kind) && !!from && !!to && /^\d{4}-\d{2}-\d{2}$/.test(date);
  const key = schedKey(from, to, date, kind);
  const [data, setData] = useState<{ key: string; v: Sched } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const retry = () => {
    schedReady.delete(key);
    schedCache.delete(key);
    setData(null);
    setAttempt((a) => a + 1);
  };
  useEffect(() => {
    if (!ok) return;
    let alive = true;
    loadSchedule(from, to, date, kind).then((v) => alive && setData({ key, v }));
    return () => {
      alive = false;
    };
  }, [key, ok, attempt]);
  if (!ok) return [null, retry];
  return [schedReady.get(key) ?? (data?.key === key ? data.v : null), retry];
}

const toMin = (hm: string): number | null => {
  const m = /^(\d{1,2}):(\d{2})/.exec(hm || "");
  return m ? +m[1] * 60 + +m[2] : null;
};

function nearLabel(diff: number, wanted: string) {
  if (diff === 0) return `ровно в ${wanted}`;
  return `ближе всего к ${wanted}: ${diff > 0 ? "на " + dur(diff) + " позже" : "на " + dur(-diff) + " раньше"}`;
}

const dur = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} ч ${m % 60 ? `${m % 60} мин` : ""}` : `${m} мин`).trim();

const KIND_WORDS: Record<string, [string, string, string]> = {
  train: ["поезд", "поезда", "поездов"],
  plane: ["рейс", "рейса", "рейсов"],
  bus: ["автобус", "автобуса", "автобусов"],
};

const PICK_WORD: Record<string, string> = { train: "поезд", plane: "рейс", bus: "автобус" };

type LegProps = {
  leg: Leg;
  f: FormState;
  set: SetF;
  driveMin?: number;
  transports: { key: string; label: string }[];
  pick: Pick;
  keep: KeepRef;
  open: boolean;
  setOpen: (v: boolean) => void;
  onPicked: () => void;
};

const fromMin = (m: number) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

function LegFields({ leg: l, f, set, driveMin, transports, pick, keep, open, setOpen, onPicked }: LegProps) {
  const tr = f[l.tk]; // туда и обратно — каждое на своём транспорте
  const auto = tr === "auto";
  const kind = auto ? "train" : tr;
  const w = LEG_WORDS[tr] || LEG_WORDS.train;

  // выбранные рейсы запоминаем отдельно для каждого вида транспорта: ушли на «Самолёт» и вернулись
  // на «Поезд» — выбранный поезд на месте
  const fields = [l.dep, l.arr, l.num, l.price] as const;
  const firstRun = useRef(true);
  const prevTr = useRef(tr);
  const saved = useRef<Record<string, Partial<FormState>>>({});
  useEffect(() => {
    if (keep.current.has(l.tk)) {
      // транспорт сменил загруженный билет вместе с номером и временем — их не стираем
      keep.current.delete(l.tk);
      firstRun.current = false;
      prevTr.current = tr;
      return;
    }
    let fresh = !f[l.num];
    if (!firstRun.current) {
      saved.current[prevTr.current] = Object.fromEntries(fields.map((k) => [k, f[k]])) as Partial<FormState>;
      const back = saved.current[tr];
      if (back && (back[l.num] || back[l.arr])) {
        for (const k of fields) set(k, back[k] as never);
        fresh = false;
      } else {
        // номер и прибытие от рейса другого вида транспорта не подходят; на машине прибытие посчитаем ниже
        set(l.num, "");
        set(l.price, 0);
        if (tr !== "car") set(l.arr, "");
        fresh = true;
      }
    }
    const wasFirst = firstRun.current;
    firstRun.current = false;
    prevTr.current = tr;
    // при загрузке открываем только «Туда»; «Обратно» — когда его транспорт выбрали отдельно
    const mine = l.tk === "transport" || (!wasFirst && f.return_transport !== tr);
    if (mine && fresh && ["train", "plane", "bus"].includes(tr)) setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tr]);
  const arrLabel = w.arr || (kind === "plane" ? "Посадка" : "Прибытие");
  const [sched, retrySched] = useSchedule(l.from, l.to, l.date, auto ? "" : kind);
  const items = sched?.available ? sched.items : [];
  const typed = normNum(f[l.num]);
  const match = typed ? items.find((t) => normNum(t.number) === typed) : undefined;
  // выбранный рейс: по номеру, а у автобусов (номеров нет) — по времени отправления и прибытия
  const selected =
    match ?? items.find((t) => !t.number && t.departure === f[l.dep] && (!f[l.arr] || t.arrival === f[l.arr]));
  // рейс, ближайший к указанному времени отправления
  const want = toMin(f[l.dep]);
  const near =
    want == null || !items.length
      ? undefined
      : items.reduce((a, b) => (Math.abs(toMin(b.departure)! - want) < Math.abs(toMin(a.departure)! - want) ? b : a));
  const target = selected ?? near;

  // цена билета «от» берётся у выбранного рейса и уходит в бюджет поездки
  const price = selected?.price_from || 0;
  useEffect(() => {
    if (sched && f[l.price] !== price) set(l.price, price);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [price, !!sched]);
  const listRef = useRef<HTMLUListElement>(null);
  const boxRef = useRef<HTMLFieldSetElement>(null);
  const hasList = !!PICK_WORD[tr] && (!sched || sched.available);
  // набирают номер — показываем подходящие рейсы, пока номер не совпал целиком
  const partial = typed && !match ? items.filter((t) => normNum(t.number).includes(typed)) : [];
  const shown = partial.length ? partial : items;

  // клик мимо поля и списка закрывает список
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    // по click, а не mousedown: список закрывается после клика, иначе страница сдвигается под курсором
    // и клик по кнопке ниже (например, «Самолёт» в «Обратно») не срабатывает
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // на машине время приезда считаем сами: выезд + время в пути; ручную правку не трогаем
  const isCar = tr === "car";
  const depMin = toMin(f[l.dep]);
  const carArr = isCar && driveMin && depMin != null ? fromMin(depMin + driveMin) : "";
  const carNextDay = isCar && driveMin && depMin != null && depMin + driveMin >= 24 * 60;
  const lastAuto = useRef("");
  const prevTransport = useRef(tr);
  useEffect(() => {
    const switched = prevTransport.current !== tr;
    prevTransport.current = tr;
    if (!carArr) return;
    if (switched || !f[l.arr] || f[l.arr] === lastAuto.current) set(l.arr, carArr);
    lastAuto.current = carArr;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [carArr, tr]);

  // открыли список или поменяли время — прокручиваем к подходящему рейсу
  useEffect(() => {
    const list = listRef.current;
    const el = list?.querySelector<HTMLElement>("[data-target]");
    if (list && el) list.scrollTop = el.offsetTop - 6; // .trips — position: relative
  }, [open, !!sched, target?.number, target?.departure]);

  // ввели время, а в него уходит ровно один рейс — подставляем его номер и прибытие
  function onDepChange(v: string) {
    set(l.dep, v);
    const same = items.filter((t) => t.departure === v);
    if (same.length === 1 && (!match || match.departure !== v)) {
      set(l.num, same[0].number);
      set(l.arr, same[0].arrival);
    }
  }

  function apply(t: Trip) {
    set(l.dep, t.departure);
    set(l.arr, t.arrival);
    set(l.num, t.number);
  }

  // номер введён вручную и найден в расписании — подставляем время
  useEffect(() => {
    if (match && (f[l.dep] !== match.departure || f[l.arr] !== match.arrival)) {
      set(l.dep, match.departure);
      set(l.arr, match.arrival);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match?.number, match?.departure]);

  const url = `https://rasp.yandex.ru/search/${kind === "car" ? "train" : kind}/?fromName=${encodeURIComponent(l.from)}&toName=${encodeURIComponent(l.to)}&when=${l.date}`;

  // билет на это направление загружен — он уже куплен: время зафиксировано, выбор и покупку не показываем
  const role = l.tk === "transport" ? "out" : "back";
  const ticket = f.bookings.find((b) => b.role === role && ["train", "plane", "bus"].includes(b.kind));
  if (ticket)
    return (
      <fieldset className="leg leg-paid">
        <legend>{l.title}</legend>
        <div className="paid-card">
          <div>
            <span className="tag ok">✓ Билет загружен · оплачено</span>
            {ticket.file ? (
              <a href={ticketFileUrl(ticket.file)} target="_blank" rel="noreferrer" title="Открыть билет">
                <b>{ticket.title} ↗</b>
              </a>
            ) : (
              <b>{ticket.title}</b>
            )}
            <span className="hint">
              {ticket.date.split("-").reverse().join(".")} · {ticket.start}
              {ticket.end ? ` → ${ticket.end}` : ""}
              {ticket.price ? ` · ${rub(ticket.price)}` : ""}
              {ticket.persons ? ` · мест: ${ticket.persons}` : ""}
            </span>
          </div>
          {/* другой билет или дорога — как «Заменить» у жилья: обычный выбор транспорта и рейса */}
          <button type="button" className="btn" onClick={() => set("bookings", f.bookings.filter((b) => b !== ticket))}>
            ⇄ Заменить
          </button>
        </div>
      </fieldset>
    );

  return (
    <fieldset className="leg" ref={boxRef}>
      <legend>{l.title}</legend>
      <div className="chips" role="group" aria-label={l.tk === "transport" ? "Транспорт туда" : "Транспорт обратно"}>
        {transports.map((t) => (
          <button
            type="button"
            key={t.key}
            className={`seg ${tr === t.key ? "on" : ""}`}
            aria-pressed={tr === t.key}
            onClick={() => pick(l.tk, t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="row">
        <label className="field">
          <span className="label">{w.dep}</span>
          <input type="time" required value={f[l.dep]} onChange={(e) => onDepChange(e.target.value)} />
        </label>
        {!auto && (
          <label className="field">
            <span className="label">{isCar && carArr ? "Приезд, расчётно" : arrLabel}</span>
            <input type="time" value={f[l.arr]} onChange={(e) => set(l.arr, e.target.value)} />
          </label>
        )}
        {w.number && (
          <label className="field">
            <span className="label">{w.number}</span>
            {hasList ? (
              <span className="with-suffix">
                <input
                  role="combobox"
                  aria-expanded={open}
                  aria-busy={!sched}
                  autoComplete="off"
                  value={f[l.num]}
                  placeholder={sched && !items.length ? "нет рейсов на эту дату" : "выберите или введите"}
                  maxLength={20}
                  onFocus={() => setOpen(true)}
                  onClick={() => setOpen(true)}
                  onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
                  onChange={(e) => {
                    set(l.num, e.target.value);
                    setOpen(true);
                  }}
                />
                <button
                  type="button"
                  className="suffix"
                  tabIndex={-1}
                  aria-label={open ? "Скрыть расписание" : "Показать расписание"}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => setOpen(!open)}
                >
                  {!sched ? <span className="spinner" aria-hidden="true" /> : open ? "▴" : "▾"}
                </button>
              </span>
            ) : (
              <input value={f[l.num]} placeholder={w.ph} maxLength={20} onChange={(e) => set(l.num, e.target.value)} />
            )}
          </label>
        )}
      </div>
      {selected && selected.departure === f[l.dep] && (
        <p className="leg-found">
          ✓ {[selected.number, selected.title].filter(Boolean).join(" ")}: {selected.departure} → {selected.arrival}
          {selected.next_day ? " (+1 день)" : ""}, {dur(selected.duration_min)}
          {selected.carrier && !selected.number ? `, ${selected.carrier}` : ""} — время подставлено
          {selected.price_from ? `; билет от ${selected.price_from.toLocaleString("ru-RU")} ₽ за взрослого — учтём в бюджете` : ""}
        </p>
      )}
      {isCar && carArr && (
        <p className="hint">
          В пути ≈ {dur(driveMin!)} с остановками{carNextDay ? ", приезд на следующий день" : ""}. Время приезда
          посчитали сами — его можно поправить.
        </p>
      )}
      {match && f[l.dep] && match.departure !== f[l.dep] && (
        <p className="hint warn">
          {PICK_WORD[tr] === "поезд" ? "Поезд" : "Рейс"} {match.number} отправляется в {match.departure}, а
          указано {f[l.dep]}.{" "}
          <button type="button" className="linkish" onClick={() => apply(match)}>
            Вернуть {match.departure}
          </button>{" "}
          или{" "}
          <button
            type="button"
            className="linkish"
            onClick={() => {
              set(l.num, "");
              set(l.arr, "");
              setOpen(true);
            }}
          >
            подобрать по времени
          </button>
        </p>
      )}
      {!match && typed && items.length > 0 && !partial.length && (
        <p className="hint">Рейса {f[l.num]} на эту дату нет в расписании — проверьте номер или выберите из списка</p>
      )}
      {!auto && kind !== "car" && (
        <div className="leg-actions">
          <a className="leg-link" href={url} target="_blank" rel="noreferrer">
            Расписание на {l.date.split("-").reverse().join(".")} на Яндексе ↗
          </a>
          {!!sched?.buy_links?.length && (
            <span className="buy-links">
              Купить билет:
              {sched.buy_links.map((b) => (
                <span key={b.url} className="ad-link">
                  <a className="leg-link" href={b.url} target="_blank" rel="noreferrer sponsored">
                    {b.label} ↗
                  </a>
                  {b.ad && <small className="ad-mark">{b.ad}</small>}
                </span>
              ))}
            </span>
          )}
        </div>
      )}
      {sched && !sched.available && sched.message && !auto && (
        <p className="hint warn">
          {sched.message}
          {sched.error && (
            <>
              {" "}
              <button type="button" className="linkish" onClick={retrySched}>
                Повторить
              </button>
            </>
          )}
        </p>
      )}
      {open && !sched && (
        <div className="trips-loading" role="status">
          <p className="loading-line">
            <span className="spinner" aria-hidden="true" /> Загружаем расписание Яндекса…
          </p>
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton" />
          ))}
        </div>
      )}
      {open && sched && items.length > 0 && (
        <p className="hint">
          В расписании Яндекса на {dateLabel(l.date)}: {items.length} {plural(items.length, ...(KIND_WORDS[tr] || KIND_WORDS.plane))}
          {near && want != null && Math.abs(toMin(near.departure)! - want) >= 180
            ? `. Ближе к ${f[l.dep]} рейсов нет — проверьте и на Яндексе по ссылке выше`
            : ""}
        </p>
      )}
      {open && sched?.price_note && shown.length > 0 && <p className="hint">{sched.price_note}</p>}
      {open && sched && shown.length > 0 && (
        <ul className="trips" ref={listRef} aria-label={`Расписание: ${l.title}`}>
          {shown.map((t) => (
            <li key={t.number + t.departure} data-target={t === target ? "" : undefined}>
              <button
                type="button"
                aria-current={t === selected ? "true" : undefined}
                className={t === selected ? "on" : t === near && !selected ? "near" : ""}
                onClick={() => {
                  apply(t);
                  setOpen(false);
                  onPicked();
                }}
              >
                <b>
                  {t.departure} → {t.arrival}
                  {t.next_day ? <sup>+1</sup> : null}
                </b>
                <span>
                  {[t.number, t.title].filter(Boolean).join(" · ")}
                  {t.cheapest && <em className="tag cheap">самый дешёвый</em>}
                  {t === selected ? (
                    <em className="tag">выбран</em>
                  ) : t === near && want != null ? (
                    <em className="tag soft">{nearLabel(toMin(t.departure)! - want, f[l.dep])}</em>
                  ) : null}
                </span>
                <small>
                  {t.from ? `${t.from} · ` : ""}
                  {dur(t.duration_min)}
                  {t.carrier ? ` · ${t.carrier}` : ""}
                  {t.classes && t.classes.length > 1
                    ? " · " + t.classes.map((c) => `${c.name} ${c.price.toLocaleString("ru-RU")} ₽`).join(", ")
                    : ""}
                </small>
                {t.price_from ? (
                  <i className="trip-price">
                    от {t.price_from.toLocaleString("ru-RU")} ₽
                    <em>{t.classes?.length === 1 ? `${t.classes[0].name} · ` : ""}за взрослого</em>
                  </i>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

function Section({ n, title, children, id }: { n: number; title: string; children: ReactNode; id?: string }) {
  return (
    <section className="sec" id={id}>
      <h2>
        <span className="num">{n}</span>
        {title}
      </h2>
      {children}
    </section>
  );
}

function Field({ label, id, children, grow, narrow }: { label: string; id: string; children: ReactNode; grow?: boolean; narrow?: boolean }) {
  return (
    <div className={`field ${grow ? "grow" : ""} ${narrow ? "narrow" : ""}`}>
      <label htmlFor={id} className="label">
        {label}
      </label>
      {children}
    </div>
  );
}

export function Stepper({ value, dec, inc, what }: { value: string; dec: () => void; inc: () => void; what: string }) {
  return (
    <div className="stepper">
      <button type="button" aria-label={`Меньше ${what}`} onClick={dec}>
        −
      </button>
      <span>{value}</span>
      <button type="button" aria-label={`Больше ${what}`} onClick={inc}>
        +
      </button>
    </div>
  );
}
