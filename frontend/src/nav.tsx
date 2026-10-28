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
