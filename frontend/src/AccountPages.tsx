import { useContext, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Logo, navigate, UserContext, UserMenu } from "./nav";
import type { User } from "./nav";

type TripRow = { id: string; title: string; city: string; dates: string; people: string; updated: string };

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `Ошибка ${r.status}`);
  return j as T;
}

/** Вход и регистрация по почте и паролю; после — onDone с пользователем. */
export function LoginPage({ onDone }: { onDone: (u: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = mode === "login" ? { email, password } : { email, password, name };
      const j = await post<{ user: User }>(`/api/auth/${mode}`, body);
      onDone(j.user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <header className="top">
        <div className="wrap top-bar">
          <Logo />
        </div>
        <div className="wrap hero auth-hero">
          <div className="eyebrow">Планировщик самостоятельных поездок</div>
          <h1>{mode === "login" ? "Вход" : "Регистрация"}</h1>
          <p>Поездки, билеты и брони хранятся в вашем аккаунте — их видите только вы.</p>
        </div>
      </header>
      <main className="wrap auth-wrap">
        <form className="card auth-card" onSubmit={submit}>
          <div className="chips" role="tablist" aria-label="Вход или регистрация">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                className={`seg ${mode === m ? "on" : ""}`}
                onClick={() => {
                  setMode(m);
                  setError("");
                }}
              >
                {m === "login" ? "Вход" : "Регистрация"}
              </button>
            ))}
          </div>
          {mode === "register" && (
            <label className="field">
              <span className="label">Как к вам обращаться</span>
              <input value={name} autoComplete="name" onChange={(e) => setName(e.target.value)} required />
            </label>
          )}
          <label className="field">
            <span className="label">Почта</span>
            <input type="email" value={email} autoComplete="email" onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="field">
            <span className="label">Пароль{mode === "register" ? " — не короче 8 символов" : ""}</span>
            <input
              type="password"
              value={password}
              minLength={mode === "register" ? 8 : undefined}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && (
            <p className="hint warn" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "Подождите…" : mode === "login" ? "Войти" : "Создать аккаунт"}
          </button>
          {mode === "login" && (
            <p className="hint">
              Забыли пароль? Восстановление по почте появится позже — пока напишите администратору прототипа.
            </p>
          )}
        </form>
      </main>
    </div>
  );
}

/** «Мои поездки»: список, открыть, удалить; смена пароля. */
export function MyTripsPage() {
  const { user, setUser } = useContext(UserContext);
  const [trips, setTrips] = useState<TripRow[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/trips")
      .then(async (r) => {
        if (r.status === 401) window.dispatchEvent(new Event("auth-expired"));
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || `Ошибка ${r.status}`);
        setTrips(j.trips);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  async function remove(t: TripRow) {
    if (!window.confirm(`Удалить поездку «${t.title}» (${t.dates})? Приложенные билеты тоже удалятся.`)) return;
    const r = await fetch(`/api/trips/${t.id}`, { method: "DELETE" });
    if (r.ok) setTrips((list) => (list || []).filter((x) => x.id !== t.id));
    else setError("Не удалось удалить поездку");
  }

  const open = (path: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    navigate(path);
  };

  return (
    <div className="page">
      <header className="top">
        <div className="wrap top-bar">
          <Logo />
          <nav className="top-nav" aria-label="Основное меню">
            <a href="/?new" onClick={open("/?new")}>
              Новая поездка
            </a>
            <UserMenu />
          </nav>
        </div>
        <div className="wrap hero auth-hero">
          <h1>Мои поездки</h1>
          <p>{user?.email}</p>
        </div>
      </header>
      <main className="wrap auth-wrap">
        <section className="card">
          <div className="row trips-head">
            <h2>Поездки</h2>
            <a className="primary as-button" href="/?new" onClick={open("/?new")}>
              + Новая поездка
            </a>
          </div>
          {error && <p className="hint warn">{error}</p>}
          {trips === null && !error && (
            <p className="loading-line" role="status">
              <span className="spinner" aria-hidden="true" /> Загружаем поездки…
            </p>
          )}
          {trips?.length === 0 && <p className="hint">Поездок пока нет — постройте первую.</p>}
          {!!trips?.length && (
            <ul className="trip-list" aria-label="Мои поездки">
              {trips.map((t) => (
                <li key={t.id}>
                  <a href={`/trip/${t.id}`} onClick={open(`/trip/${t.id}`)}>
                    <b>{t.title}</b>
                    <span>
                      {t.dates} · {t.people}
                    </span>
                  </a>
                  <button type="button" className="btn" aria-label={`Удалить «${t.title}»`} onClick={() => remove(t)}>
                    Удалить
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <ChangePassword onSessionRenewed={() => user && setUser(user)} />
      </main>
    </div>
  );
}

function ChangePassword({ onSessionRenewed }: { onSessionRenewed: () => void }) {
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <form
      className="card"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await post("/api/auth/password", { old, new: next });
          setOld("");
          setNext("");
          setMsg({ ok: true, text: "Пароль изменён. На других устройствах нужно войти заново." });
          onSessionRenewed();
        } catch (err) {
          setMsg({ ok: false, text: (err as Error).message });
        }
      }}
    >
      <h2>Сменить пароль</h2>
      <div className="row">
        <label className="field">
          <span className="label">Текущий пароль</span>
          <input type="password" value={old} autoComplete="current-password" onChange={(e) => setOld(e.target.value)} required />
        </label>
        <label className="field">
          <span className="label">Новый пароль</span>
          <input type="password" value={next} minLength={8} autoComplete="new-password" onChange={(e) => setNext(e.target.value)} required />
        </label>
      </div>
      {msg && <p className={`hint ${msg.ok ? "ok" : "warn"}`}>{msg.text}</p>}
      <button type="submit" className="btn">
        Сменить пароль
      </button>
    </form>
  );
}
