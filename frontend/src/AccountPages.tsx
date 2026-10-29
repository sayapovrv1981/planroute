import { useContext, useEffect, useId, useRef, useState } from "react";
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

/** Поле с подписью и подсказкой под ним. Подсказка — рядом с полем, а не внутри подписи: иначе она
 *  попадает в название поля для скринридера и для тестов. */
function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => React.ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label className="label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

/** Поле пароля с кнопкой «показать»: иначе опечатку в скрытом вводе не найти. */
function PasswordField({ label, hint, value, onChange, autoComplete, min }: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: "current-password" | "new-password";
  min?: number;
}) {
  const [shown, setShown] = useState(false);
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <span className="pw">
          <input
            id={id}
            type={shown ? "text" : "password"}
            value={value}
            minLength={min}
            autoComplete={autoComplete}
            onChange={(e) => onChange(e.target.value)}
            required
          />
          <button type="button" className="pw-eye" aria-pressed={shown} onClick={() => setShown(!shown)}
                  title={shown ? "Скрыть пароль" : "Показать пароль"}>
            {shown ? "Скрыть" : "Показать"}
          </button>
        </span>
      )}
    </Field>
  );
}

type Mode = "login" | "register" | "forgot";

/** Вход, регистрация и письмо для забытого пароля; после входа — onDone с пользователем. */
export function LoginPage({ onDone }: { onDone: (u: User) => void }) {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  // переключили вкладку — поля пустые: чужие данные и подстановки браузера не должны оставаться
  function go(m: Mode) {
    setMode(m);
    setError("");
    setSent("");
    setPassword("");
    setName("");
    if (m !== "login") setEmail("");
    form.current?.reset();
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "forgot") {
        await post("/api/auth/forgot", { email });
        setSent(email);
        setEmail("");
      } else {
        const body = mode === "login" ? { email, password } : { email, password, name };
        const j = await post<{ user: User }>(`/api/auth/${mode}`, body);
        setPassword("");
        onDone(j.user);
      }
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page auth-page">
      <header className="top">
        <div className="wrap top-bar">
          <Logo />
        </div>
        <div className="wrap hero auth-hero">
          <div className="eyebrow">Планировщик самостоятельных поездок</div>
          <h1>{mode === "register" ? "Регистрация" : mode === "forgot" ? "Забыли пароль" : "Вход"}</h1>
          <p>Поездки, билеты и брони хранятся в вашем аккаунте — их видите только вы.</p>
        </div>
      </header>
      <main className="wrap auth-wrap">
        <form className="card auth-card" onSubmit={submit} autoComplete={mode === "login" ? "on" : "off"} ref={form}>
          <div className="chips" role="tablist" aria-label="Вход или регистрация">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                className={`seg ${mode === m ? "on" : ""}`}
                onClick={() => go(m)}
              >
                {m === "login" ? "Вход" : "Регистрация"}
              </button>
            ))}
          </div>
          {mode === "forgot" ? (
            sent ? (
              <>
                <p className="hint ok" role="status">
                  Письмо со ссылкой отправлено на {sent}. Ссылка работает 2 часа. Если письма нет через пару минут,
                  посмотрите в папке «Спам».
                </p>
                <button type="button" className="primary" onClick={() => go("login")}>
                  Вернуться ко входу
                </button>
              </>
            ) : (
              <>
                <p className="hint">Пришлём на почту ссылку, по которой можно задать новый пароль.</p>
                <Field label="Почта аккаунта">
                  {(id) => (
                    <input id={id} type="email" value={email} placeholder="ivan@example.ru" autoComplete="email"
                           onChange={(e) => setEmail(e.target.value)} required />
                  )}
                </Field>
              </>
            )
          ) : (
            <>
              {mode === "register" && (
                <Field label="Как к вам обращаться" hint="Имя увидите только вы — так подписываем приветствие.">
                  {(id) => (
                    <input id={id} value={name} placeholder="Иван" autoComplete="name" maxLength={80}
                           onChange={(e) => setName(e.target.value)} required />
                  )}
                </Field>
              )}
              <Field label="Почта" hint={mode === "register" ? "На неё придёт ссылка, если забудете пароль." : undefined}>
                {(id) => (
                  <input id={id} type="email" value={email} placeholder="ivan@example.ru"
                         autoComplete={mode === "login" ? "username" : "email"}
                         onChange={(e) => setEmail(e.target.value)} required />
                )}
              </Field>
              <PasswordField
                label="Пароль"
                hint={mode === "register" ? "Не короче 8 символов. Лучше не тот, что в других сервисах." : undefined}
                value={password}
                onChange={setPassword}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                min={mode === "register" ? 8 : undefined}
              />
            </>
          )}
          {error && (
            <p className="hint warn" role="alert">
              {error}
            </p>
          )}
          {!(mode === "forgot" && sent) && (
            <button type="submit" className="primary" disabled={busy}>
              {busy ? "Подождите…" : mode === "login" ? "Войти" : mode === "register" ? "Создать аккаунт" : "Прислать ссылку"}
            </button>
          )}
          {mode === "login" && (
            <button type="button" className="link-btn" onClick={() => go("forgot")}>
              Забыли пароль?
            </button>
          )}
          {mode === "forgot" && !sent && (
            <button type="button" className="link-btn" onClick={() => go("login")}>
              Вспомнил, вернуться ко входу
            </button>
          )}
          {mode === "register" && (
            <p className="hint">Регистрируясь, вы соглашаетесь на хранение поездок и приложенных билетов в аккаунте.</p>
          )}
        </form>
      </main>
    </div>
  );
}

/** Новый пароль по ссылке из письма (/reset?token=…): проверяем токен, задаём пароль и сразу входим. */
export function ResetPage({ onDone }: { onDone: (u: User) => void }) {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [owner, setOwner] = useState<User | null | undefined>(undefined); // undefined — ещё проверяем ссылку
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`/api/auth/reset?token=${encodeURIComponent(token)}`)
      .then((r) => (r.ok ? r.json() : { user: null }))
      .then((j) => setOwner(j.user ?? null))
      .catch(() => setOwner(null));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== again) {
      setError("Пароли не совпадают");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const j = await post<{ user: User }>("/api/auth/reset", { token, password });
      onDone(j.user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page auth-page">
      <header className="top">
        <div className="wrap top-bar">
          <Logo />
        </div>
        <div className="wrap hero auth-hero">
          <h1>Новый пароль</h1>
          <p>{owner ? owner.email : "Ссылка из письма"}</p>
        </div>
      </header>
      <main className="wrap auth-wrap">
        <form className="card auth-card" onSubmit={submit} autoComplete="off">
          {owner === undefined && (
            <p className="loading-line" role="status">
              <span className="spinner" aria-hidden="true" /> Проверяем ссылку…
            </p>
          )}
          {owner === null && (
            <>
              <p className="hint warn" role="alert">
                Ссылка устарела или уже использована. Запросите письмо заново.
              </p>
              <button type="button" className="primary" onClick={() => navigate("/login")}>
                К входу
              </button>
            </>
          )}
          {owner && (
            <>
              <PasswordField label="Новый пароль" hint="Не короче 8 символов" value={password} onChange={setPassword}
                             autoComplete="new-password" min={8} />
              <PasswordField label="Ещё раз" value={again} onChange={setAgain} autoComplete="new-password" min={8} />
              {error && (
                <p className="hint warn" role="alert">
                  {error}
                </p>
              )}
              <button type="submit" className="primary" disabled={busy}>
                {busy ? "Подождите…" : "Сохранить и войти"}
              </button>
              <p className="hint">На других устройствах придётся войти заново.</p>
            </>
          )}
        </form>
      </main>
    </div>
  );
}

/** «Мои поездки»: список, открыть, удалить. Аккаунт — отдельной страницей (AccountPage). */
export function MyTripsPage() {
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
      </main>
    </div>
  );
}

/** Страница аккаунта (/account, пункт меню с именем): имя, почта и смена пароля. */
export function AccountPage() {
  const { user, setUser } = useContext(UserContext);
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
          <h1>Пользователь</h1>
        </div>
      </header>
      <main className="wrap auth-wrap">
        <section className="card account">
          <dl className="account-facts">
            <div>
              <dt>Имя</dt>
              <dd>{user?.name}</dd>
            </div>
            <div>
              <dt>Почта</dt>
              <dd>{user?.email}</dd>
            </div>
          </dl>
        </section>
        <section className="card account-pw">
          <h2>Сменить пароль</h2>
          <ChangePassword onSessionRenewed={() => user && setUser(user)} />
        </section>
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
      <div className="row">
        <PasswordField label="Текущий пароль" value={old} onChange={setOld} autoComplete="current-password" />
        <PasswordField label="Новый пароль" hint="Не короче 8 символов" value={next} onChange={setNext}
                       autoComplete="new-password" min={8} />
      </div>
      {msg && <p className={`hint ${msg.ok ? "ok" : "warn"}`}>{msg.text}</p>}
      <button type="submit" className="btn">
        Сменить пароль
      </button>
    </form>
  );
}
