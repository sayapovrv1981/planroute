import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { wikiPhoto } from "./api";
import type { Alternative } from "./types";

// Общие части выбора мест: на странице поездки («⇄ Заменить», «+ добавить пункт») и в форме («Где жить»)

export function Photo({ title, alt, area }: { title: string; alt: string; area?: boolean }) {
  const [ph, setPh] = useState<{ src: string; page: string } | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    wikiPhoto(title).then((p) => live && setPh(p));
    return () => {
      live = false;
    };
  }, [title]);
  if (!ph) return null;
  return (
    <a
      className="photo"
      href={ph.page}
      target="_blank"
      rel="noreferrer"
      title={area ? `Район: ${title}. Место выберете по ссылке` : "Фото и статья в Википедии"}
    >
      <img src={ph.src} alt={area ? `Район: ${title}` : alt} loading="lazy" />
      {area && <span className="photo-cap">район</span>}
    </a>
  );
}

/** Фото самого места (объявление на Суточно.ру); по клику — его страница. */
export function OwnPhoto({ src, href, alt }: { src: string; href?: string | null; alt: string }) {
  const img = <img src={src} alt={alt} loading="lazy" />;
  return href ? (
    <a className="photo" href={href} target="_blank" rel="noreferrer" title="Открыть объявление на сайте">
      {img}
    </a>
  ) : (
    <span className="photo">{img}</span>
  );
}

/** Своя квартира по ссылке «Поделиться» с Суточно.ру. */
export function StayImport({
  search,
  onSubmit,
  title = "Своё жильё — найти на даты поездки:",
  placeholder = "https://spb.sutochno.ru/361449",
  hint,
}: {
  search: { label: string; url: string; ad?: string | null }[];
  onSubmit: (url: string) => Promise<boolean>;
  title?: string;
  placeholder?: string;
  hint?: ReactNode;
}) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  // не <form>: блок стоит и внутри формы новой поездки, а вложенная форма отправила бы её целиком
  const submit = async () => {
    if (!url.trim() || busy) return;
    setBusy(true);
    if (await onSubmit(url)) setUrl("");
    setBusy(false);
  };
  return (
    <div className="custom-add stay-import">
      <label className="field grow">
        <span className="label">
          {title}{" "}
          {search.map((s, i) => (
            <span key={s.url}>
              {i > 0 && ", "}
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.label} ↗
              </a>
            </span>
          ))}
        </span>
        <input
          value={url}
          placeholder={placeholder}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
        {search
          .filter((s) => s.ad)
          .map((s) => (
            <small key={s.url} className="ad-mark">
              {s.label}: {s.ad}
            </small>
          ))}
      </label>
      <button type="button" className="primary" disabled={busy || !url.trim()} onClick={submit}>
        {busy ? "Загружаем…" : "Подставить"}
      </button>
      {hint ?? (
        <p className="hint">
          Квартиру с Суточно.ру можно подставить сюда: в объявлении нажмите «Поделиться» → «Скопировать ссылку» и вставьте
          её — подтянем фото, адрес, цену и время заезда и выезда. Бронь на другом сайте (Авито, Trip.com, Островок)
          введите вручную ниже.
        </p>
      )}
    </div>
  );
}

const norm = (s: string) => s.toLowerCase().replace(/ё/g, "е");

/** Список вариантов с поиском по названию: пустая строка — снова все варианты, порядок — как прислал сервер
 * (по рейтингу, затем по расстоянию до предыдущего места). */
export function AltList({
  items,
  page,
  onPick,
  end,
  placeholder = "Поиск по названию, например «пельмени» или «Тюбетей»",
}: {
  items: Alternative[];
  page: number;
  onPick: (a: Alternative) => void;
  end?: ReactNode;
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(page);
  const found = useMemo(() => {
    const words = norm(q).split(/\s+/).filter(Boolean);
    return words.length ? items.filter((a) => words.every((w) => norm(`${a.name} ${a.meta}`).includes(w))) : items;
  }, [items, q]);
  return (
    <>
      {items.length > page && (
        <input
          type="search"
          className="alt-search"
          aria-label="Поиск по названию"
          placeholder={placeholder}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setShown(page);
          }}
        />
      )}
      {found.slice(0, shown).map((a) => (
        <AltRow key={a.id} a={a} onPick={() => onPick(a)} />
      ))}
      {q && found.length === 0 && <p className="hint">Ничего не нашлось — проверьте название или очистите поиск.</p>}
      {shown < found.length ? (
        <button type="button" className="btn more" onClick={() => setShown(shown + page)}>
          Ещё варианты ({found.length - shown})
        </button>
      ) : (
        !q && end
      )}
    </>
  );
}

export function AltRow({ a, onPick }: { a: Alternative; onPick: () => void }) {
  return (
    <div className="alt">
      {a.photo ? <OwnPhoto src={a.photo} alt={a.name} /> : a.wiki && <Photo title={a.wiki} alt={a.name} area={a.wiki_area} />}
      <div>
        <b>{a.name}</b>
        {(a.rating || a.dist) && (
          <span className="alt-dist">{[a.rating ? `★ ${String(a.rating).replace(".", ",")}` : "", a.dist || ""].filter(Boolean).join(" · ")}</span>
        )}
        <span>{a.meta}</span>
      </div>
      <button type="button" className="btn" onClick={onPick}>
        Выбрать
      </button>
    </div>
  );
}
