import { useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import type { AskChange, AskResult, Trip } from "./types";

/** «Поменять словами»: просьба -> список изменений с галочками -> применить выбранные. */
export function AskEdit({ trip, onTrip }: { trip: Trip; onTrip: (t: Trip) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [done, setDone] = useState<{ applied: string[]; skipped: string[] } | null>(null);

  async function ask(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setDone(null);
    try {
      const r = await api.ask(trip.id, text);
      setResult(r);
      setPicked(new Set(r.changes.map((_, i) => i)));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!result) return;
    setBusy(true);
    setError("");
    try {
      const actions: AskChange[] = result.changes.filter((_, i) => picked.has(i));
      const t = await api.applyAsk(trip.id, actions);
      onTrip(t);
      setDone(t.edit || null);
      setResult(null);
      setText("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggle(i: number) {
    const next = new Set(picked);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setPicked(next);
  }

  if (!open) {
    return (
      <button type="button" className="ghost" onClick={() => setOpen(true)}>
        ✎ Поменять программу словами
      </button>
    );
  }

  return (
    <div className="ask-edit">
      <form onSubmit={ask}>
        <label htmlFor="ask-text">Что поменять в программе?</label>
        <textarea
          id="ask-text"
          rows={2}
          maxLength={1000}
          value={text}
          placeholder="Например: во второй день поменьше ходить, добавь театр, обед в первый день — татарская кухня"
          onChange={(e) => setText(e.target.value)}
        />
        <div className="ask-row">
          <button type="submit" className="primary" disabled={busy || !text.trim()}>
            {busy && !result ? "Думаем…" : "Предложить изменения"}
          </button>
          <button type="button" className="btn" onClick={() => (setOpen(false), setResult(null), setError(""))}>
            Закрыть
          </button>
        </div>
      </form>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="ask-result">
          {result.summary && <p className="wishes-note">{result.summary}</p>}
          {result.changes.length > 0 ? (
            <ul className="ask-changes">
              {result.changes.map((c, i) => (
                <li key={i}>
                  <label>
                    <input type="checkbox" checked={picked.has(i)} onChange={() => toggle(i)} /> {c.label}
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p className="note">Изменений, которые можно сделать в программе, не нашлось.</p>
          )}
          {result.skipped.map((s, i) => (
            <p key={i} className="note">Не получится: {s}</p>
          ))}
          {result.unclear && <p className="note">{result.unclear}</p>}
          {result.changes.length > 0 && (
            <div className="ask-row">
              <button type="button" className="primary" disabled={busy || picked.size === 0} onClick={apply}>
                Применить выбранное
              </button>
              <button type="button" className="btn" onClick={() => setResult(null)}>
                Отмена
              </button>
            </div>
          )}
        </div>
      )}
      {done && (
        <div className="ask-result">
          {done.applied.length > 0 && <p className="wishes-note">Готово: {done.applied.join("; ")}.</p>}
          {done.skipped.map((s, i) => (
            <p key={i} className="note">Не получилось: {s}</p>
          ))}
        </div>
      )}
    </div>
  );
}
