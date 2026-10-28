export type Transfer = { minutes: number; km: number; mode: string };

export type DocFile = { name: string; filename: string; url?: string };

export type Doc = {
  id: string;
  files: DocFile[];
  title: string;
  sum: number;
  paid: number;
  onsite: number;
  date: string | null;
  start: string | null;
  end: string | null;
};

export type Alternative = {
  id: string;
  name: string;
  meta: string;
  wiki?: string | null;
  wiki_area?: boolean;
  photo?: string | null;
  rating?: number | null;
  /** примерное расстояние от предыдущего места, км */
  km?: number | null;
  /** «≈ 1,2 км от предыдущего места, пешком 15 мин» */
  dist?: string | null;
};

export type Item = {
  id: string;
  kind: "travel" | "hotel" | "food" | "museum" | "walk" | "sight" | "daytrip" | "excursion" | "theatre" | "rest" | "custom";
  meal: string | null;
  wiki: string | null;
  wiki_area?: boolean;
  /** Своё фото места (квартира с Суточно.ру), миниатюра */
  photo?: string | null;
  photos?: { src: string; big: string }[];
  /** Страница места на сайте — по ней ведёт название пункта */
  href?: string | null;
  href_ad?: string | null;
  /** Поиск жилья на даты и в городе поездки (у пунктов с жильём) */
  /** экскурсии Трипстера к этому месту: ссылка партнёрская, ad — пометка «Реклама» */
  excursions?: { id: number; title: string; meta: string; url: string; photo?: string | null; ad: string }[];
  /** пункт, связанный с жильём: checkin, checkin_late, wake, breakfast, checkout, back */
  stay_role?: string;
  title: string;
  note: string;
  place_id: string | null;
  lat: number | null;
  lon: number | null;
  duration: number;
  default_duration: number;
  fixed_start: number | null;
  cost: { kind: "none" | "ticket" | "booking" | "receipt"; estimate: number; basis?: string };
  doc: Doc | null;
  /** ad — маркировка рекламы («Реклама · рекламодатель · erid»), если ссылка партнёрская с erid */
  links: { label: string; url: string; ad?: string | null }[];
  alternatives: Alternative[];
  user_changed: boolean;
  start: number;
  end: number;
  transfer: Transfer | null;
  wait: number;
  warnings: string[];
};

export type Day = { index: number; date: string; label: string; sub: string; items: Item[] };

export type Budget = {
  limit: number;
  total: number;
  paid: number;
  onsite: number;
  estimate: number;
  left: number;
  exact_share: number;
  over: boolean;
  status: "ok" | "tolerance" | "over";
  tolerance_pct: number;
  hard_limit: number;
  categories: { key: string; name: string; sum: number; ok: number; n: number }[];
};

export type TripInput = {
  origin: string;
  city: string;
  start_date: string;
  start_time: string;
  return_time: string;
  days: number;
  transport: string;
  /** на чём обратно; у старых поездок нет — как туда */
  return_transport?: string;
  /** выбранное в форме жильё из каталога */
  stay_id?: string;
  /** своя бронь: ссылка на объявление Суточно.ру */
  stay_url?: string;
  /** своя бронь на другом сайте, введённая вручную */
  stay_manual?: Record<string, unknown> | null;
  /** загруженные в форме билеты и брони */
  bookings?: Record<string, unknown>[];
  adults: number;
  kids_ages: number[];
  budget: number;
  pace: string;
  wishes: string;
  tags: string[];
  end_date: string;
  home_address: string;
  to_station_min: number;
  boarding_min: number | null;
  sight_buffer_min: number;
  step_min: number;
  min_rating: number;
  budget_tolerance_pct: number;
  stay: string;
};

export type Trip = {
  id: string;
  input: TripInput;
  summary: {
    title: string;
    city: string;
    dates: string;
    people: string;
    transport: string;
    pace: string;
    wishes: string[];
    /** как модель поняла пожелания; нет, если разбирали по ключевым словам */
    wishes_note?: string | null;
  };
  days: Day[];
  budget: Budget;
};

export type Meta = {
  /** город поездки -> город отправления -> минуты на машине */
  drive_minutes?: Record<string, Record<string, number>>;
  cities: string[];
  transports: { key: string; label: string }[];
  paces: { key: string; label: string }[];
  stays: { key: string; label: string }[];
  wishes: { key: string; label: string }[];
  interests?: { key: string; label: string }[];
  boarding_default: Record<string, number>;
};
