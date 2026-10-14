import { Link, useSearchParams } from "react-router";
import { api } from "../api";
import { Failed, Loading, Pager, Pill, useLoad } from "../components/common";
import { borrowerHref, LoanLink } from "../components/records";
import { day, naira, stamp } from "../format";

const PAGE = 50;

// Each bucket's tile, by how late: yellow, then peach, then mustard; recovery is coral (people, not messages).
const TILE: Record<string, { className: string; style?: React.CSSProperties }> = {
  reminder: { className: "tile yellow", style: { minHeight: 0 } },
  follow_up: { className: "tile peach", style: { minHeight: 0 } },
  arrangement: { className: "tile", style: { background: "var(--mustard)", minHeight: 0 } },
  recovery: { className: "tile", style: { background: "var(--coral)", color: "#ffffff", minHeight: 0 } },
};
const KIND_PILL: Record<string, string> = { reminder: "suspense", follow_up: "proposed", arrangement: "two", broken_promise: "late" };
const FILTERS: [string, string][] = [
  ["", "All due"],
  ["broken_promise", "Promise broken"],
  ["reminder", "Reminder"],
  ["follow_up", "Follow-up"],
  ["arrangement", "Arrangement"],
];

/** The collections morning: loans in arrears by bucket, and which are due a message today. */
export function Collections() {
  const [params, setParams] = useSearchParams();
  const bucket = params.get("bucket") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const { data, error, reload } = useLoad(() => api.collections({ bucket, limit: PAGE, offset }), [bucket, offset]);

  function set(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next);
  }

  if (error) return <Failed error={error} retry={reload} />;
  if (!data) return <Loading what="collections" />;
  const late = data.buckets.reduce((n, b) => n + b.loans, 0);

  return (
    <>
      <section className="head">
        <div>
          <span className="eyebrow">Under the collections policy</span>
          <h1>Collections</h1>
          <p className="sub">
            {late.toLocaleString()} loans are late. Messages are drafted under the bank's policy and go out only after a
            portfolio manager approves them, between 08:00 and 18:00, Monday to Saturday.
          </p>
        </div>
      </section>

      <section className="tiles" aria-label="Loans in arrears, by how late">
        {data.buckets.map((b) => {
          const t = TILE[b.bucket] ?? { className: "tile" };
          return (
            <div key={b.bucket} className={t.className} style={t.style}>
              <span className="k">
                {b.label} · {b.days} days
              </span>
              <span className="v">{b.loans.toLocaleString()}</span>
              <span>{naira(b.arrears_kobo)} in arrears</span>
              {b.bucket === "recovery" && <span>Handled by the recovery team: no drafted messages.</span>}
            </div>
          );
        })}
      </section>

      <section className="stats" aria-label="Messages and promises">
        <div className="stat">
          <span className="k">Due a message today</span>
          <span className="v">{data.due.total.toLocaleString()}</span>
          <span className="d">{bucket ? `${FILTERS.find((f) => f[0] === bucket)?.[1]} only` : "every kind"}</span>
        </div>
        <div className="stat">
          <span className="k">Drafts waiting for approval</span>
          <span className="v">{data.drafts_waiting.toLocaleString()}</span>
          <span className="d">
            the drafting agent writes them · <Link to="/messages">decide</Link>
          </span>
        </div>
        <div className="stat">
          <span className="k">Promises to pay, open</span>
          <span className="v">{data.promises.open.toLocaleString()}</span>
          <span className="d">left alone until the promised date</span>
        </div>
        <div className="stat">
          <span className="k">Promises kept · broken</span>
          <span className="v">
            {data.promises.kept.toLocaleString()} · {data.promises.broken.toLocaleString()}
          </span>
          <span className="d">a broken promise comes back to the top</span>
        </div>
      </section>

      <div className="filters" role="group" aria-label="Kind of message">
        {FILTERS.map(([k, label]) => (
          <button key={k || "all"} className="chip" aria-pressed={bucket === k} onClick={() => set({ bucket: k, offset: "" })}>
            {label}
          </button>
        ))}
      </div>

      <section className="panel">
        <header>
          <h2>Due a message today</h2>
          <span className="note">
            Late, no promise in date, no draft waiting, fewer than two messages this week
            {data.directory_copied_at && ` · from the directory copied ${stamp(data.directory_copied_at)}`}
          </span>
        </header>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Loan</th>
                <th>Borrower</th>
                <th className="num">Days late</th>
                <th className="num">Arrears</th>
                <th>Message</th>
                <th>Last message</th>
                <th>Promise</th>
              </tr>
            </thead>
            <tbody>
              {data.due.items.map((d) => (
                <tr key={d.loan_ref}>
                  <td>
                    <LoanLink loanRef={d.loan_ref} />
                  </td>
                  <td>
                    <Link to={borrowerHref(d.borrower_ref)}>{d.borrower_name}</Link>
                    <br />
                    <span className="ch">{d.branch_name ?? ""}</span>
                  </td>
                  <td className="num">{d.days_late}</td>
                  <td className="num">{naira(d.arrears_kobo)}</td>
                  <td>
                    <Pill status={KIND_PILL[d.bucket] ?? "new"} label={d.bucket_label} />
                  </td>
                  <td className="ch">{d.last_sent_at ? stamp(d.last_sent_at) : "none yet"}</td>
                  <td className="ch">
                    {d.promise
                      ? `${d.promise.status} · ${naira(d.promise.amount_kobo)} by ${day(d.promise.due_on)}`
                      : "–"}
                  </td>
                </tr>
              ))}
              {data.due.items.length === 0 && (
                <tr>
                  <td colSpan={7} className="empty">
                    Nobody is due a message{bucket ? " of this kind" : ""} today.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pager offset={offset} limit={PAGE} total={data.due.total} onPage={(o) => set({ offset: o ? String(o) : "" })} />
      </section>
    </>
  );
}
