import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { FormPage } from "./FormPage";
import { TripPage } from "./TripPage";
import "./styles.css";

function App() {
  const [loc, setLoc] = useState(window.location.pathname + window.location.search);
  useEffect(() => {
    const on = () => setLoc(window.location.pathname + window.location.search);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  const m = loc.match(/^\/trip\/([a-z0-9]+)/i);
  return m ? <TripPage key={m[1]} id={m[1]} /> : <FormPage key={loc} />;
}

createRoot(document.getElementById("root")!).render(<App />);
