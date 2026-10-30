import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api, dateLabel, fmtDur, fmtHM, plural, rub } from "./api";
import { Logo, navigate, UserMenu } from "./nav";
import { AltList, OwnPhoto, Photo, StayImport } from "./Picks";
import type { Alternative, Day, Item, Trip } from "./types";

const KIND: Record<Item["kind"], { label: string; cls: string }> = {
  travel: { label: "Дорога", cls: "k-road" },
  hotel: { label: "Жильё", cls: "k-hotel" },
  food: { label: "Еда", cls: "k-food" },
  museum: { label: "Музей", cls: "k-museum" },
  sight: { label: "Достопримечательность", cls: "k-museum" },
  walk: { label: "Прогулка", cls: "k-walk" },
  daytrip: { label: "Выезд за город", cls: "k-walk" },
  excursion: { label: "Экскурсия с гидом", cls: "k-museum" },
  theatre: { label: "Театр", cls: "k-theatre" },
  rest: { label: "Отдых", cls: "k-rest" },
  custom: { label: "Свой пункт", cls: "k-rest" },
};

// места программы (как TRIPSTER_KINDS в planner.py) и экскурсия, которая встала вместо такого места
const SIGHT_KINDS: Item["kind"][] = ["museum", "sight", "walk", "daytrip", "excursion"];

const ATTACH_LABEL: Record<string, string> = {
  ticket: "Приложить билет",
  booking: "Приложить бронь",
  receipt: "Добавить чек",
};

type Actions = {
  duration: (it: Item, delta: number) => void;
  replace: (it: Item, placeId: string) => void;
  putPlace: (it: Item, url: string) => Promise<boolean>;
  attach: (it: Item, form: FormData) => void;
  detach: (it: Item) => void;
  removeFile: (it: Item, name: string) => void;
  remove: (it: Item) => void;
  add: (after: string | null, body: { place_id?: string; title?: string; duration?: number }) => void;
};

export function TripPage({ id }: { id: string }) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [day, setDay] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    api.getTrip(id).then(setTrip).catch((e) => setError(e.message));
  }, [id]);

  // «К пункту»: после переключения дня прокрутить к карточке и подсветить её
  useEffect(() => {
    if (!focus) return;
    const el = document.getElementById(`item-${focus}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      const t = setTimeout(() => setFocus(null), 2200);
      return () => clearTimeout(t);
    }
  }, [focus, day]);

  async function run(p: Promise<Trip>) {
    setBusy(true);
    setError("");
    try {
      setTrip(await p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!trip) {
    return (
      <div className="page">
        <header className="top">
          <div className="wrap top-bar">
            <Logo />
          </div>
        </header>
        <main className="wrap">
          <p className="loading">{error || "Загружаем поездку…"}</p>
        </main>
      </div>
    );
  }

  const di = Math.min(day, trip.days.length - 1);
  const d = trip.days[di];
  const step = trip.input.step_min || 5;
  const actions: Actions = {
    duration: (it, delta) => run(api.setDuration(trip.id, it.id, it.duration + delta)),
    replace: (it, placeId) => run(api.replace(trip.id, it.id, placeId)),
    putPlace: async (it, url) => {
      setBusy(true);
      setError("");
      try {
        setTrip(await api.putPlace(trip.id, it.id, url));
        return true;
      } catch (e) {
        setError((e as Error).message);
        return false;
      } finally {
        setBusy(false);
      }
    },
    attach: (it, form) => run(api.attachDoc(trip.id, it.id, form)),
    detach: (it) => run(api.detachDoc(trip.id, it.id)),
    removeFile: (it, name) => run(api.removeFile(trip.id, it.id, name)),
    remove: (it) => {
      if (window.confirm(`Убрать «${it.title}» из программы?`)) run(api.deleteItem(trip.id, it.id));
    },
    add: (after, body) => run(api.addItem(trip.id, di, { after, ...body })),
  };
  const editInputs = () => navigate(`/?edit=${trip.id}`);

  return (
    <div className="page">
      <header className="top">
        <div className="wrap top-bar">
          <Logo />
          <nav className="top-nav" aria-label="Основное меню">
            <a
              href={`/?edit=${trip.id}`}
              onClick={(e) => {
                e.preventDefault();
                editInputs();
              }}
            >
              Изменить вводные
            </a>
            <a
              href="/?new"
              onClick={(e) => {
                e.preventDefault();
                navigate("/?new");
              }}
            >
              Новая поездка
            </a>
            <UserMenu />
          </nav>
        </div>
      </header>

      <section className="trip-head">
        <div className="wrap">
          <button type="button" className="back" onClick={editInputs}>
            ← Изменить вводные: откуда, куда, даты
          </button>
          <h1>{trip.summary.title}</h1>
          <div className="pills">
            <span>{trip.summary.dates}</span>
            <span>
              {trip.input.origin} → {trip.summary.city}, {trip.summary.transport}
            </span>
            <span>{trip.summary.people}</span>
            <span>бюджет {rub(trip.input.budget)}</span>
            <span>{trip.summary.pace}</span>
            {trip.input.wishes && <span className="accent">{trip.input.wishes}</span>}
          </div>
          {trip.summary.wishes_note && <p className="wishes-note">Как мы поняли пожелания: {trip.summary.wishes_note}</p>}
          <div className="head-actions">
            <button type="button" className="ghost" onClick={() => navigator.clipboard?.writeText(window.location.href)}>
              Скопировать ссылку
            </button>
            <button type="button" className="ghost" onClick={() => window.print()}>
              Распечатать
            </button>
          </div>
        </div>
      </section>

      <main className="wrap trip-layout">
        <div className="trip-main">
          <div className="tabs" role="tablist" aria-label="Дни поездки">
            {trip.days.map((x, i) => (
              <button
                key={x.index}
                role="tab"
                aria-selected={i === di}
                className={i === di ? "on" : ""}
                onClick={() => setDay(i)}
              >
                <b>{x.label}</b>
                <small>{x.sub}</small>
              </button>
            ))}
          </div>
          <DayStats day={d} />
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <ol className={`timeline ${busy ? "busy" : ""}`}>
            {d.items.map((it, idx) => (
              <ItemRow
                key={it.id}
                it={it}
                prev={d.items.slice(0, idx).reverse().find((x) => x.lat !== null) || null}
                step={step}
                focused={focus === it.id}
                tripId={trip.id}
                dayIndex={di}
                actions={actions}
              />
            ))}
          </ol>
          <AddPanel tripId={trip.id} dayIndex={di} after={d.items.length ? d.items[d.items.length - 1].id : null} actions={actions} label="+ Добавить пункт в конец дня" step={step} />
        </div>

        <aside className="side">
          <BudgetCard trip={trip} />
          <DocsCard
            trip={trip}
            onPick={(dayIndex, itemId) => {
              setDay(dayIndex);
              setFocus(itemId);
            }}
          />
          <MapCard day={d} />
        </aside>
      </main>
    </div>
  );
}

function DayStats({ day }: { day: Day }) {
  if (!day.items.length) return null;
  const first = day.items[0];
  const last = day.items[day.items.length - 1];
  const km = day.items.reduce((s, it) => s + (it.transfer && it.transfer.mode === "пешком" ? it.transfer.km : 0), 0);
  const cost = day.items.reduce((s, it) => s + (it.doc ? it.doc.sum : it.cost.estimate), 0);
  return (
    <div className="day-stats">
      <span>
        <b>{day.items.length}</b> пунктов
      </span>
      <span>
        {fmtHM(first.start)}–{fmtHM(last.end)}
      </span>
      <span>≈ {km.toFixed(1).replace(".", ",")} км пешком</span>
      <span>
        Расходы дня на всех: <b>{rub(cost)}</b>
      </span>
    </div>
  );
}

function routeLink(a: Item, b: Item, walking: boolean) {
  return `https://yandex.ru/maps/?rtext=${a.lat},${a.lon}~${b.lat},${b.lon}&rtt=${walking ? "pd" : "mt"}`;
}

/** Лента фото объявления; по клику — фото в полном размере. */
function Gallery({ photos }: { photos: { src: string; big: string }[] }) {
  return (
    <div className="gallery">
      {photos.map((p, i) => (
        <a key={p.big} href={p.big} target="_blank" rel="noreferrer" aria-label={`Фото ${i + 1} из ${photos.length}`}>
          <img src={p.src} alt="" loading="lazy" />
        </a>
      ))}
    </div>
  );
}

function ItemRow({
  it,
  prev,
  step,
  focused,
  tripId,
  dayIndex,
  actions,
}: {
  it: Item;
  prev: Item | null;
  step: number;
  focused: boolean;
  tripId: string;
  dayIndex: number;
  actions: Actions;
}) {
  const [open, setOpen] = useState(false);
  const [docForm, setDocForm] = useState(false);
  const [adding, setAdding] = useState(false);
  const k = KIND[it.kind] || KIND.custom;
  const ticketTime = !!it.doc && !!it.doc.start && it.kind !== "hotel";
  const scheduled = it.kind === "travel" && it.fixed_start !== null && it.cost.kind === "ticket";
  const locked = ticketTime && (!!it.doc?.end || it.kind === "travel" || it.kind === "theatre");
  const hasCost = it.cost.kind !== "none" || !!it.doc;
  const canDelete = !(it.kind === "travel" && it.cost.kind === "ticket");
  const walk = it.transfer?.mode === "пешком";
  const canReplace = it.alternatives.length > 0 && !locked;
  // у достопримечательностей «Заменить» — сразу у названия
  const sight = SIGHT_KINDS.includes(it.kind);
  const replaceBtn = (
    <button type="button" className={`btn ${open ? "on" : ""}`} aria-expanded={open} onClick={() => setOpen(!open)}>
      ⇄ Заменить
    </button>
  );

  return (
    <li className={`tl-item ${focused ? "focused" : ""}`} id={`item-${it.id}`}>
      {it.transfer && (
        <div className="tl-transfer">
          ↓ {fmtDur(it.transfer.minutes)} {it.transfer.mode}
          {it.transfer.km ? `, ${String(it.transfer.km).replace(".", ",")} км` : ""}
          {prev && prev.lat !== null && it.lat !== null && (
            <a href={routeLink(prev, it, walk)} target="_blank" rel="noreferrer">
              {walk ? "пеший маршрут" : "маршрут"} в Яндекс Картах ↗
            </a>
          )}
        </div>
      )}
      {it.wait >= 30 && <div className="tl-transfer free">Свободное время · {fmtDur(it.wait)}</div>}
      <div className="tl-row">
        <div className="tl-time">
          <b>{fmtHM(it.start)}</b>
          <span>до {fmtHM(it.end)}</span>
        </div>
        <div className={`tl-dot ${k.cls}`} aria-hidden="true" />
        <article className="tl-card">
          <div className="tags">
            <span className={`kind ${k.cls}`}>{k.label}</span>
            {it.doc && <span className="tag ok">{it.doc.files?.length ? "✓ Документ приложен" : "✓ Оплачено"}</span>}
            {it.user_changed && <span className="tag mine">Изменено вами</span>}
            {canDelete && (
              <button type="button" className="icon-btn" aria-label={`Убрать «${it.title}» из программы`} title="Убрать из программы" onClick={() => actions.remove(it)}>
                ×
              </button>
            )}
          </div>
          {it.warnings.length > 0 && (
            <ul className="warnings">
              {it.warnings.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          )}
          <div className="title-row">
            {it.photo ? (
              <OwnPhoto src={it.photo} href={it.href} alt={it.title} />
            ) : (
              it.wiki && <Photo title={it.wiki} alt={it.title} area={it.wiki_area} />
            )}
            <div>
              <h3>
                {it.href ? (
                  <a className="title-link" href={it.href} target="_blank" rel="noreferrer" title="Открыть объявление на сайте">
                    {it.title} ↗
                  </a>
                ) : (
                  it.title
                )}
              </h3>
              {it.note && <p className="note">{it.note}</p>}
              {!!it.why?.length && <p className="why">Почему здесь: {it.why.join(" · ")}</p>}
              {it.href_ad && <small className="ad-mark">{it.href_ad}</small>}
            </div>
            {sight && canReplace && <div className="title-action">{replaceBtn}</div>}
          </div>
          {it.photos && it.photos.length > 1 && <Gallery photos={it.photos} />}

          {hasCost && (
            <div className={`cost ${it.doc ? "is-ok" : ""}`}>
              {it.doc ? (
                <>
                  <div className="cost-row">
                    <div>
                      <b>{it.doc.title}</b>
                      <span>
                        {it.doc.onsite > 0 ? `оплачено ${rub(it.doc.paid)} · на месте ${rub(it.doc.onsite)}` : "оплачено полностью"}
                        {it.doc.date ? ` · ${dateLabel(it.doc.date)}` : ""}
                        {it.doc.start ? ` · ${it.doc.start}${it.doc.end ? `–${it.doc.end}` : ""}` : ""}
                      </span>
                    </div>
                    <strong>{rub(it.doc.sum)}</strong>
                    <div className="cost-actions">
                      <button type="button" className="btn small" onClick={() => setDocForm(true)}>
                        Изменить / ещё файл
                      </button>
                      <button type="button" className="btn small" aria-label="Убрать документ" onClick={() => actions.detach(it)}>
                        ×
                      </button>
                    </div>
                  </div>
                  {it.doc.files.length > 0 && (
                    <ul className="files">
                      {it.doc.files.map((f) => (
                        <li key={f.name}>
                          <a href={f.url} target="_blank" rel="noreferrer">
                            📄 {f.filename}
                          </a>
                          <button type="button" aria-label={`Убрать файл ${f.filename}`} onClick={() => actions.removeFile(it, f.name)}>
                            ×
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : (
                <div className="cost-row">
                  <div>
                    <b>≈ {rub(it.cost.estimate)}</b>{" "}
                    <span>
                      {it.kind === "food" ? it.cost.basis : `${it.cost.basis ? `${it.cost.basis} · ` : ""}оценка — станет точной по документу`}
                    </span>
                  </div>
                  <button type="button" className="btn dashed" onClick={() => setDocForm(true)}>
                    ⤒ {ATTACH_LABEL[it.cost.kind] || "Приложить документ"}
                  </button>
                </div>
              )}
              {docForm && (
                <DocForm
                  it={it}
                  onCancel={() => setDocForm(false)}
                  onSubmit={(f) => {
                    setDocForm(false);
                    actions.attach(it, f);
                  }}
                />
              )}
            </div>
          )}

          {!!it.excursions?.length && (
            // экскурсии Трипстера к этому месту — свёрнуты, чтобы не загромождать программу
            <details className="excursions">
              <summary>
                Экскурсии с гидом сюда — {it.excursions.length} {plural(it.excursions.length, "вариант", "варианта", "вариантов")} на Трипстере
              </summary>
              {it.excursions.map((x) => (
                <div key={x.id} className="excursion">
                  <a href={x.url} target="_blank" rel="noreferrer sponsored">
                    {x.photo && <img src={x.photo} alt="" loading="lazy" />}
                    <span>
                      <b>{x.title} ↗</b>
                      <span>{x.meta}</span>
                      <small className="ad-mark">{x.ad}</small>
                    </span>
                  </a>
                  {/* экскурсия вместо места: цена — в бюджет, длительность — в расписание */}
                  <button type="button" className="btn small" onClick={() => actions.replace(it, `tripster-${x.id}`)}>
                    Выбрать
                  </button>
                </div>
              ))}
            </details>
          )}

          <div className="card-foot">
            <div className="links">
              {it.links.map((l) =>
                l.ad ? (
                  <span key={l.url + l.label} className="ad-link">
                    <a href={l.url} target="_blank" rel="noreferrer">
                      {l.label} ↗
                    </a>
                    <small className="ad-mark">{l.ad}</small>
                  </span>
                ) : (
                  <a key={l.url + l.label} href={l.url} target="_blank" rel="noreferrer">
                    {l.label} ↗
                  </a>
                ),
              )}
            </div>
            <div className="controls">
              {locked || scheduled ? (
                <span className="locked">
                  {locked ? "Время из документа" : "По расписанию"} · {fmtDur(it.duration)}
                </span>
              ) : (
                <div className="stepper sm">
                  <button type="button" aria-label={`Короче на ${step} минут`} onClick={() => actions.duration(it, -step)} disabled={it.duration <= step}>
                    −
                  </button>
                  <span>{fmtDur(it.duration)}</span>
                  <button type="button" aria-label={`Длиннее на ${step} минут`} onClick={() => actions.duration(it, step)}>
                    +
                  </button>
                </div>
              )}
              {canReplace && !sight && replaceBtn}
              {(it.stay_role === "checkin" || it.stay_role === "checkin_late") && (
                // жильё задаёт весь маршрут — меняем в вводных, и программа строится заново
                <button
                  type="button"
                  className="btn"
                  title="Откроются вводные поездки на разделе «Где жить» — маршрут перестроится под новое жильё"
                  onClick={() => navigate(`/?edit=${tripId}&focus=stay`)}
                >
                  ⇄ Изменить жильё
                </button>
              )}
            </div>
          </div>

          {open && (
            <div className="alts">
              {it.kind === "food" && (
                <StayImport
                  title="Своё кафе или ресторан —"
                  placeholder="https://yandex.ru/maps/org/…"
                  search={
                    it.lat != null
                      ? [{ label: "найти рядом на Яндекс Картах", url: `https://yandex.ru/maps/?ll=${it.lon},${it.lat}&z=16&text=${encodeURIComponent("кафе")}` }]
                      : []
                  }
                  hint={
                    <p className="hint">
                      В Яндекс Картах откройте заведение → «Поделиться» → «Скопировать ссылку» и вставьте её сюда: подтянем
                      адрес, часы работы, средний чек и рейтинг, а время в программе пересчитаем.
                    </p>
                  }
                  onSubmit={async (url) => {
                    const ok = await actions.putPlace(it, url);
                    if (ok) setOpen(false);
                    return ok;
                  }}
                />
              )}
              <div className="alts-title">Варианты рядом с маршрутом</div>
              <AltList
                items={it.alternatives}
                page={4}
                placeholder={it.kind === "food" ? undefined : "Поиск по названию, например «Кремль» или «экскурсия»"}
                onPick={(a) => {
                  setOpen(false);
                  actions.replace(it, a.id);
                }}
                end={<p className="hint">Это все варианты из каталога прототипа. С данными Яндекса их станет намного больше.</p>}
              />
            </div>
          )}
        </article>
      </div>
      <div className="insert">
        {adding ? (
          <AddPanel tripId={tripId} dayIndex={dayIndex} after={it.id} actions={actions} onDone={() => setAdding(false)} startOpen step={step} />
        ) : (
          <button type="button" className="insert-btn" onClick={() => setAdding(true)} aria-label={`Добавить пункт после «${it.title}»`}>
            + добавить пункт
          </button>
        )}
      </div>
    </li>
  );
}

function AddPanel({
  tripId,
  dayIndex,
  after,
  actions,
  label,
  onDone,
  startOpen,
  step,
}: {
  tripId: string;
  dayIndex: number;
  after: string | null;
  actions: Actions;
  label?: string;
  step: number;
  onDone?: () => void;
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!startOpen);
  const [places, setPlaces] = useState<Alternative[] | null>(null);
  const [origin, setOrigin] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [dur, setDur] = useState(60);

  useEffect(() => {
    if (open && places === null)
      api
        .suggest(tripId, dayIndex, after)
        .then((r) => {
          setPlaces(r.places);
          setOrigin(r.origin);
        })
        .catch(() => setPlaces([]));
  }, [open, places, tripId, dayIndex, after]);

  const close = () => {
    setOpen(false);
    onDone?.();
  };

  if (!open) {
    return (
      <button type="button" className="btn wide add-end" onClick={() => setOpen(true)}>
        {label || "+ Добавить пункт"}
      </button>
    );
  }
  return (
    <div className="add-panel">
      <div className="alts-title">Добавить в программу</div>
      {places === null && <p className="hint">Подбираем места рядом…</p>}
      {origin && <p className="hint">Здесь вы ещё в городе {origin} — места оттуда пока не подбираем. Добавьте свой пункт.</p>}
      {places && (
        <AltList
          items={places}
          page={5}
          onPick={(p) => {
            close();
            actions.add(after, { place_id: p.id });
          }}
        />
      )}
      <form
        className="custom-add"
        onSubmit={(e) => {
          e.preventDefault();
          if (!title.trim()) return;
          close();
          actions.add(after, { title, duration: dur });
        }}
      >
        <label className="field grow">
          <span className="label">Или свой пункт</span>
          <input value={title} placeholder="Например: встреча с друзьями, шопинг" onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="field narrow">
          <span className="label">Минут</span>
          <input type="number" min={step} max={600} step={step} value={dur} onChange={(e) => setDur(Number(e.target.value))} />
        </label>
        <button type="submit" className="primary">
          Добавить
        </button>
        <button type="button" className="btn" onClick={close}>
          Отмена
        </button>
      </form>
    </div>
  );
}

function DocForm({ it, onCancel, onSubmit }: { it: Item; onCancel: () => void; onSubmit: (f: FormData) => void }) {
  const [sum, setSum] = useState(it.doc ? String(it.doc.sum) : "");
  const [paid, setPaid] = useState(it.doc ? String(it.doc.paid) : "");
  const withTime = it.kind !== "hotel" && it.kind !== "food";
  const total = Number(sum.replace(/\s/g, "")) || 0;
  const paidN = paid === "" ? total : Number(paid.replace(/\s/g, "")) || 0;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (!f.get("paid")) f.set("paid", String(total));
    for (const v of f.getAll("file")) {
      if (v instanceof File && !v.name) f.delete("file");
    }
    onSubmit(f);
  }

  return (
    <form className="doc-form" onSubmit={submit}>
      <p>
        Приложите один или несколько файлов — скриншоты, PDF, фото — и укажите данные из документа. Сверим их с
        программой и предупредим о расхождениях. Распознавание файлов появится в MVP.
      </p>
      <div className="row">
        <label className="field grow">
          <span className="label">{it.doc?.files.length ? "Добавить файлы" : "Файлы"}</span>
          <input type="file" name="file" multiple accept="image/*,.pdf" />
        </label>
        <label className="field grow">
          <span className="label">Что это</span>
          <input name="title" defaultValue={it.doc?.title || ""} placeholder="Например: бронь № 4817, 2 ночи" />
        </label>
      </div>
      <div className="row">
        <label className="field">
          <span className="label">Сумма, ₽</span>
          <input name="sum" inputMode="numeric" required value={sum} onChange={(e) => setSum(e.target.value)} />
        </label>
        <label className="field">
          <span className="label">Уже оплачено, ₽</span>
          <input name="paid" inputMode="numeric" value={paid} placeholder={sum || "всё"} onChange={(e) => setPaid(e.target.value)} />
        </label>
        <label className="field">
          <span className="label">{it.kind === "hotel" ? "Дата заезда" : "Дата в документе"}</span>
          <input name="date" type="date" defaultValue={it.doc?.date || ""} />
        </label>
      </div>
      {withTime && (
        <div className="row">
          <label className="field">
            <span className="label">Время по билету: с</span>
            <input name="start" type="time" defaultValue={it.doc?.start || ""} />
          </label>
          <label className="field">
            <span className="label">до (если указано)</span>
            <input name="end" type="time" defaultValue={it.doc?.end || ""} />
          </label>
        </div>
      )}
      {total > 0 && paidN < total && <p className="hint">Оплатить на месте: {rub(total - paidN)}</p>}
      <div className="row actions">
        <button type="submit" className="primary">
          Сохранить
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

function BudgetCard({ trip }: { trip: Trip }) {
  const b = trip.budget;
  const scale = Math.max(b.hard_limit || b.limit, b.total);
  const pct = (n: number) => `${Math.min(100, (n / scale) * 100)}%`;
  const statusText =
    b.status === "ok"
      ? `Остаток бюджета: ${rub(b.left)}`
      : b.status === "tolerance"
        ? `Бюджет превышен на ${rub(-b.left)} — в пределах допустимых ${b.tolerance_pct}%`
        : `Бюджет превышен на ${rub(-b.left)} — больше допустимых ${b.tolerance_pct}%. Замените дорогие пункты или поднимите бюджет`;
  return (
    <section className="card budget" aria-labelledby="budget-title">
      <div className="card-title">
        <h2 id="budget-title">Бюджет поездки</h2>
        <span>
          <b className={b.status}>{rub(b.total)}</b> из {rub(b.limit)}
        </span>
      </div>
      <div className="bar" aria-hidden="true">
        <i className="paid" style={{ width: pct(b.paid) }} />
        <i className="onsite" style={{ width: pct(b.onsite) }} />
        <i className="est" style={{ width: pct(b.estimate) }} />
        {b.tolerance_pct > 0 && <span className="limit-mark" style={{ left: pct(b.limit) }} />}
      </div>
      <div className="legend">
        <div>
          <span className="sw paid" />
          Оплачено
          <b>{rub(b.paid)}</b>
        </div>
        <div>
          <span className="sw onsite" />
          На месте
          <b>{rub(b.onsite)}</b>
        </div>
        <div>
          <span className="sw est" />
          Оценка
          <b>≈ {rub(b.estimate)}</b>
        </div>
      </div>
      <p className={`note-box ${b.status}`}>
        Точно известно <b>{b.exact_share}%</b> расходов. {statusText}
      </p>
      <table className="cats">
        <caption className="sr">Расходы по категориям</caption>
        <tbody>
          {b.categories.map((c) => (
            <tr key={c.key}>
              <th scope="row">{c.name}</th>
              <td>
                <span className={`tag ${c.ok === c.n ? "ok" : c.ok ? "part" : "est"}`}>
                  {c.ok === c.n ? "точно" : c.ok ? `частично ${c.ok}/${c.n}` : "оценка"}
                </span>
              </td>
              <td className="amount">
                {c.ok ? "" : "≈ "}
                {rub(c.sum)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Оценки — по средним ценам каталога прототипа. Точные суммы — из приложенных документов.</p>
    </section>
  );
}

function DocsCard({ trip, onPick }: { trip: Trip; onPick: (day: number, itemId: string) => void }) {
  const docs = useMemo(
    () =>
      trip.days.flatMap((d) =>
        d.items.filter((it) => it.cost.kind === "ticket" || it.cost.kind === "booking").map((it) => ({ it, day: d })),
      ),
    [trip],
  );
  const done = docs.filter((x) => x.it.doc).length;
  return (
    <section className="card" aria-labelledby="docs-title">
      <div className="card-title">
        <h2 id="docs-title">Билеты и брони</h2>
        <span>
          <b>{done}</b> из {docs.length} на месте
        </span>
      </div>
      <ul className="docs">
        {docs.map(({ it, day }) => {
          const warn = it.doc && it.warnings.length > 0;
          return (
            <li key={it.id}>
              <span className={`doc-ico ${it.doc ? (warn ? "warn" : "ok") : ""}`}>{it.doc ? (warn ? "!" : "✓") : "—"}</span>
              <div>
                <b>{it.title}</b>
                <span>
                  {day.sub}, {fmtHM(it.start)} · {it.doc ? (warn ? "есть расхождения" : "приложено") : "не приложено"}
                  {it.doc ? ` · ${rub(it.doc.sum)}` : ""}
                </span>
              </div>
              <button type="button" className="btn small" onClick={() => onPick(day.index, it.id)}>
                К пункту
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function MapCard({ day }: { day: Day }) {
  const pts = day.items.filter((it) => it.lat !== null && it.lon !== null);
  const rtext = pts.map((p) => `${p.lat},${p.lon}`).join("~");
  return (
    <section className="card">
      <div className="card-title">
        <h2>Маршрут дня</h2>
      </div>
      <p className="hint">
        {pts.length} точек в порядке посещения. Пешие отрезки между пунктами открываются ссылками «пеший маршрут» прямо в
        ленте — удобно вести прогулку в Яндекс Картах.
      </p>
      <a className="btn wide" href={`https://yandex.ru/maps/?rtext=${rtext}&rtt=pd`} target="_blank" rel="noreferrer">
        Весь день пешком в Яндекс Картах ↗
      </a>
      <a className="btn wide" href={`https://yandex.ru/maps/?rtext=${rtext}&rtt=mt`} target="_blank" rel="noreferrer">
        Весь день на транспорте ↗
      </a>
    </section>
  );
}
