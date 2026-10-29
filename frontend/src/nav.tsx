import { createContext, useContext } from "react";

export function navigate(path: string) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

export function Logo() {
  return (
    <a
      href="/"
      className="logo"
      onClick={(e) => {
        e.preventDefault();
        navigate("/");
      }}
    >
      <svg width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <rect width="32" height="32" rx="9" fill="#C8371E" />
        <path d="M9 22c3-1 4-5 7-6s5 1 7-3" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
        <circle cx="9" cy="22" r="2.4" fill="#fff" />
        <circle cx="23" cy="13" r="2.4" fill="#fff" />
      </svg>
      Своим ходом
    </a>
  );
}


export type User = { id: number; email: string; name: string };

/** Кто вошёл: null — никто (показываем вход). setUser(null) после выхода или истёкшей сессии. */
export const UserContext = createContext<{ user: User | null; setUser: (u: User | null) => void }>({
  user: null,
  setUser: () => undefined,
});

/** Пункты шапки для вошедшего: «Мои поездки», имя (страница аккаунта), «Выйти».
 *  Ставится внутрь <nav className="top-nav">; подсвечена текущая страница. */
export function UserMenu() {
  const { user, setUser } = useContext(UserContext);
  if (!user) return null;
  const path = window.location.pathname;
  return (
    <>
      <a
        href="/trips"
        aria-current={path === "/trips" ? "page" : undefined}
        onClick={(e) => {
          e.preventDefault();
          navigate("/trips");
        }}
      >
        Мои поездки
      </a>
      <a
        href="/account"
        className="user-name"
        title={user.email}
        aria-current={path === "/account" ? "page" : undefined}
        onClick={(e) => {
          e.preventDefault();
          navigate("/account");
        }}
      >
        {user.name}
      </a>
      <button
        type="button"
        className="top-link"
        onClick={async () => {
          await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
          try {
            localStorage.removeItem("svoim-hodom:form-draft"); // черновик формы — не для следующего на этом компьютере
          } catch {
            /* хранилище недоступно */
          }
          setUser(null);
          navigate("/login");
        }}
      >
        Выйти
      </button>
    </>
  );
}
