import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { FormPage } from "./FormPage";
import { LoginPage, MyTripsPage, ResetPage } from "./AccountPages";
import { navigate, UserContext } from "./nav";
import type { User } from "./nav";
import { TripPage } from "./TripPage";
import "./styles.css";

function App() {
  const [loc, setLoc] = useState(window.location.pathname + window.location.search);
  const [user, setUser] = useState<User | null | undefined>(undefined); // undefined — ещё проверяем сессию
  useEffect(() => {
    const on = () => setLoc(window.location.pathname + window.location.search);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : { user: null }))
      .then((j) => setUser(j.user ?? null))
      .catch(() => setUser(null));
    const expired = () => setUser(null);
    window.addEventListener("auth-expired", expired);
    return () => window.removeEventListener("auth-expired", expired);
  }, []);
  if (user === undefined) return null;

  const path = window.location.pathname;
  let page;
  if (path === "/reset") {
    // ссылка из письма: новый пароль задаём и когда в этом браузере уже кто-то вошёл
    page = (
      <ResetPage
        onDone={(u) => {
          setUser(u);
          navigate("/trips");
        }}
      />
    );
  } else if (!user) {
    // без входа — только вход и регистрация; после входа вернём туда, куда шли
    const next = path === "/login" ? new URLSearchParams(window.location.search).get("next") || "/" : loc;
    page = (
      <LoginPage
        onDone={(u) => {
          setUser(u);
          navigate(next.startsWith("/") && !next.startsWith("//") ? next : "/");
        }}
      />
    );
  } else if (path === "/login") {
    page = <FormPage key={loc} />;
  } else if (path === "/trips") {
    page = <MyTripsPage />;
  } else {
    const m = loc.match(/^\/trip\/([a-z0-9]+)/i);
    page = m ? <TripPage key={m[1]} id={m[1]} /> : <FormPage key={loc} />;
  }
  return <UserContext.Provider value={{ user, setUser }}>{page}</UserContext.Provider>;
}

createRoot(document.getElementById("root")!).render(<App />);
