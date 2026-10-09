import { useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api, type Summary } from "../api";
import { naira } from "../format";
import { useSession } from "../session";

interface Area {
  to: string;
  label: string;
  /** Not built yet: says when it arrives. */
  later?: string;
  count?: (s: Summary) => number;
}

export const AREAS: { label: string; areas: Area[] }[] = [
  {
    label: "Work",
    areas: [
      { to: "/today", label: "Today", later: "chunk 9" },
      { to: "/review", label: "Review queue", later: "chunk 6", count: (s) => s.pending_proposals },
      { to: "/payments", label: "Payments", count: (s) => Object.values(s.lines).reduce((a, b) => a + b, 0) - s.lines.duplicate },
    ],
  },
  {
    label: "Records",
    areas: [
      { to: "/borrowers", label: "Borrowers", later: "chunk 7" },
      { to: "/loans", label: "Loans", later: "chunk 7" },
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

const ROLE: Record<string, string> = { officer: "Officer", supervisor: "Supervisor", auditor: "Auditor, read only" };

export function Shell() {
  const { staff, config, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [search, setSearch] = useState("");

  // Navigation counts, refreshed on each move between screens. Cheap: two counts.
  useEffect(() => {
    api.summary().then(setSummary, () => {});
  }, [location.pathname]);

  function onSearch(e: FormEvent) {
    e.preventDefault();
    const q = search.trim();
    if (q) navigate(`/payments?q=${encodeURIComponent(q)}`);
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
                  {a.count && summary ? (
                    <span className="count">{a.count(summary).toLocaleString()}</span>
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
              placeholder="Search payments: name, narration, loan or line id"
              aria-label="Search payments"
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
