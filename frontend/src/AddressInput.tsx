import { useEffect, useRef, useState } from "react";

export type AddressPick = {
  value: string;
  full?: string;
  lat: number | null;
  lon: number | null;
  to_station_min?: number;
  station?: string;
};

type Props = {
  id: string;
  value: string;
  origin: string;
  /** город поездки: от него зависит вокзал отправления (в Казань — с Казанского) */
  city: string;
  transport: string;
  onChange: (v: string) => void;
  onPick: (s: AddressPick) => void;
};

/**
 * Поле «улица и дом» с подсказками в городе отправления (DaData или OpenStreetMap через наш сервер).
 * Город показывается приставкой слева и в поле не вводится. Стрелки, Enter, Esc.
 */
export function AddressInput({ id, value, origin, city, transport, onChange, onPick }: Props) {
  const [items, setItems] = useState<AddressPick[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [note, setNote] = useState("");
  const picked = useRef<string | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (value.trim().length < 3 || picked.current === value) {
      setItems([]);
      return;
    }
    const my = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const q = new URLSearchParams({ q: value, origin, city, transport });
        const r = await fetch(`/api/address/suggest?${q}`);
        const j = await r.json();
        if (my !== seq.current) return;
        setItems(j.suggestions || []);
        setNote(j.error || "");
        setOpen(true);
        setActive(-1);
      } catch {
        if (my === seq.current) setNote("Сервис подсказок адресов недоступен — введите адрес вручную");
      }
    }, 300);
    return () => clearTimeout(t);
  }, [value, origin, city, transport]);

  function pick(s: AddressPick) {
    picked.current = s.value;
    onPick(s);
    setOpen(false);
    setItems([]);
  }

  const listId = `${id}-list`;
  const showList = open && items.length > 0;

  return (
    <div className="combo">
      <div className="with-prefix">
        {origin && <span className="prefix">{origin},</span>}
      <input
        id={id}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showList}
        aria-controls={listId}
        aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        autoComplete="off"
        value={value}
        placeholder="улица и дом, например Тверская 7"
        onChange={(e) => {
          picked.current = null;
          onChange(e.target.value);
        }}
        onFocus={() => items.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (!showList) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(items.length - 1, a + 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(0, a - 1));
          } else if (e.key === "Enter" && active >= 0) {
            e.preventDefault();
            pick(items[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      </div>
      {showList && (
        <ul className="combo-list" id={listId} role="listbox">
          {items.map((s, i) => (
            <li
              key={s.value + i}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? "on" : ""}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s);
              }}
            >
              <span>{s.value}</span>
              {s.to_station_min ? <small>≈ {s.to_station_min} мин до вокзала</small> : null}
            </li>
          ))}
        </ul>
      )}
      {note && <p className="hint">{note}</p>}
    </div>
  );
}
