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

// The top bar shows Work and Records, then System after a divider. Areas still to come (Later) are
// named in the footer instead, so the bar holds only places a person can work in.
export const AREAS: { label: string; areas: Area[] }[] = [
  {
    label: "Work",
    areas: [
      { to: "/today", label: "Today", count: (s) => s.failed_postings, alert: true },
      { to: "/review", label: "Review queue", count: (s) => s.pending_proposals },
      { to: "/collections", label: "Collections" },
      // Drafts to approve and replies to deal with: both are the portfolio manager's.
      { to: "/messages", label: "Messages", count: (s) => s.drafts_waiting + s.replies_waiting },
      { to: "/payments", label: "Payments", count: (s) => Object.values(s.lines).reduce((a, b) => a + b, 0) - s.lines.duplicate },
    ],
  },
  {
    label: "Records",
    areas: [
      { to: "/borrowers", label: "Borrowers" },
      { to: "/loans", label: "Loans" },
      { to: "/runs", label: "Agent runs" },
    ],
  },
  {
    label: "Later",
    areas: [
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

const ROLE: Record<string, string> = { officer: "Officer", supervisor: "Supervisor", auditor: "Auditor, read only", manager: "Portfolio manager" };

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase();
}

function AreaLink({ area, summary }: { area: Area; summary: Summary | null }) {
  const n = count(area, summary);
  return (
    <NavLink to={area.to}>
      <span>{area.label}</span>
      {n !== null ? (
        <span className={area.alert ? "count alert" : "count"} title={area.alert ? "Postings that stopped" : undefined}>
          {n.toLocaleString()}
        </span>
      ) : area.later ? (
        <span className="later">soon</span>
      ) : null}
    </NavLink>
  );
}

export function Shell() {
  const { staff, config, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);

  // Navigation counts, refreshed on each move between screens and after any decision. Cheap: two counts.
  const refresh = useCallback(() => {
    api.summary().then(setSummary, () => {});
  }, []);
  useEffect(refresh, [location.pathname, refresh]);
  useOnChange(refresh);
  useEffect(() => setMenuOpen(false), [location.pathname]); // a phone's menu closes on arrival

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

  const group = (label: string) => AREAS.find((g) => g.label === label)?.areas ?? [];
  const later = group("Later");

  return (
    <div className="app">
      <header className="topbar" data-open={menuOpen}>
        {/* Row one: whose bank, search, and who is signed in, kept apart on the right. */}
        <div className="topbar-in bar-top">
          <NavLink to="/today" className="brand">
            <b>Repaydesk</b>
            <small>{config?.bank_name ?? " "}</small>
          </NavLink>
          <form role="search" className="search" onSubmit={onSearch}>
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search a loan, customer ref or phone"
              aria-label="Search"
            />
          </form>
          <div className="account-group">
            <span className="proto" title="Every borrower, loan and payment here is synthetic">
              Prototype
            </span>
            {staff && (
              <div className="account" aria-label="Signed in">
                <span className="avatar" aria-hidden="true">
                  {initials(staff.name)}
                </span>
                <span className="who">
                  <b>{staff.name}</b>
                  <span className="role">
                    {ROLE[staff.role]}
                    {staff.limit_kobo > 0 && ` · limit ${naira(staff.limit_kobo)}`}
                  </span>
                </span>
                <button className="btn signout" onClick={signOut}>
                  Sign out
                </button>
              </div>
            )}
          </div>
          <button
            type="button"
            className="menu-btn"
            aria-label={menuOpen ? "Close the menu" : "Open the menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
              {menuOpen ? <path d="M4 4l10 10M14 4L4 14" /> : <path d="M2 4.5h14M2 9h14M2 13.5h14" />}
            </svg>
          </button>
        </div>
        {/* Row two: where to go. */}
        <div className="topbar-in bar-nav">
          <nav className="nav" aria-label="Main">
            {[...group("Work"), ...group("Records")].map((a) => (
              <AreaLink key={a.to} area={a} summary={summary} />
            ))}
            <span className="sep" aria-hidden="true" />
            {group("System").map((a) => (
              <AreaLink key={a.to} area={a} summary={summary} />
            ))}
          </nav>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
      <footer className="foot">
        <div className="foot-in">
          <span>Repaydesk, a prototype. Every borrower, loan and payment here is synthetic.</span>
          <span>Still to come: {later.map((a) => `${a.label} (${a.later})`).join(", ")}</span>
        </div>
      </footer>
    </div>
  );
}
