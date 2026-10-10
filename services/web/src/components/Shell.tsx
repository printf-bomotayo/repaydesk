import { useCallback, useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api, type Summary } from "../api";
import { naira } from "../format";
import { useSession } from "../session";
import { useOnChange } from "./common";
import { borrowerHref, loanHref } from "./records";

interface Area {
  to: string;
  label: string;
  /** Not built yet: says when it arrives. */
  later?: string;
  count?: (s: Summary) => number;
  /** The count is trouble: shown only when there is some, and in the warning colour. */
  alert?: boolean;
}

export const AREAS: { label: string; areas: Area[] }[] = [
  {
    label: "Work",
    areas: [
      { to: "/today", label: "Today", count: (s) => s.failed_postings, alert: true },
      { to: "/review", label: "Review queue", count: (s) => s.pending_proposals },
      { to: "/payments", label: "Payments", count: (s) => Object.values(s.lines).reduce((a, b) => a + b, 0) - s.lines.duplicate },
    ],
  },
  {
    label: "Records",
    areas: [
      { to: "/borrowers", label: "Borrowers" },
      { to: "/loans", label: "Loans" },
      { to: "/runs", label: "Agent runs", later: "milestone 3" },
    ],
  },
  {
    label: "Later",
    areas: [
      { to: "/collections", label: "Collections", later: "milestone 4" },
      { to: "/measures", label: "Measures", later: "milestone 5" },
    ],
  },
  {
    label: "System",
    areas: [
      { to: "/ingestion", label: "Ingestion" },
      { to: "/audit", label: "Audit log" },
    ],
  },
];

/** The number beside an area, or null for none: an alert count shows only when there is trouble. */
function count(a: Area, summary: Summary | null): number | null {
  const n = a.count && summary ? a.count(summary) : undefined;
  if (typeof n !== "number" || Number.isNaN(n) || (a.alert && n === 0)) return null;
  return n;
}

const ROLE: Record<string, string> = { officer: "Officer", supervisor: "Supervisor", auditor: "Auditor, read only" };

export function Shell() {
  const { staff, config, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [search, setSearch] = useState("");

  // Navigation counts, refreshed on each move between screens and after any decision. Cheap: two counts.
  const refresh = useCallback(() => {
    api.summary().then(setSummary, () => {});
  }, []);
  useEffect(refresh, [location.pathname, refresh]);
  useOnChange(refresh);

  // A loan reference, customer reference or phone opens that record; anything else searches payments.
  async function onSearch(e: FormEvent) {
    e.preventDefault();
    const q = search.trim();
    if (!q) return;
    const found = await api.find(q).catch(() => ({ to: "payments" as const, q }));
    if (found.to === "loan") navigate(loanHref(found.ref));
    else if (found.to === "borrower") navigate(borrowerHref(found.ref));
    else navigate(`/payments?q=${encodeURIComponent(q)}`);
  }

  return (
    <div className="app">
      <aside className="side">
        <div className="brand">
          <b>Repaydesk</b>
          <small>{config?.bank_name ?? " "}</small>
        </div>
        {AREAS.map((group) => (
          <div key={group.label}>
            <div className="nav-label">{group.label}</div>
            <nav className="nav" aria-label={group.label}>
              {group.areas.map((a) => (
                <NavLink key={a.to} to={a.to}>
                  <span>{a.label}</span>
                  {count(a, summary) !== null ? (
                    <span className={a.alert ? "count alert" : "count"} title={a.alert ? "Postings that stopped" : undefined}>
                      {count(a, summary)!.toLocaleString()}
                    </span>
                  ) : a.later ? (
                    <span className="later">{a.later}</span>
                  ) : null}
                </NavLink>
              ))}
            </nav>
          </div>
        ))}
        <div className="side-foot">Synthetic data. Every borrower and payment is generated.</div>
      </aside>
      <div className="main">
        <header className="top">
          <form role="search" onSubmit={onSearch}>
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search: a loan, customer ref or phone opens it; anything else searches payments"
              aria-label="Search"
            />
          </form>
          <span className="proto">Prototype</span>
          {staff && (
            <span className="who">
              <span>
                <b>{staff.name}</b>
                <br />
                <span className="role">
                  {ROLE[staff.role]}
                  {staff.limit_kobo > 0 && ` · limit ${naira(staff.limit_kobo)}`}
                </span>
              </span>
              <button className="btn" onClick={signOut}>
                Sign out
              </button>
            </span>
          )}
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
