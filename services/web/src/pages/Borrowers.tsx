import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api, type BorrowerView } from "../api";
import { ExternalLink, Failed, Loading, Pager, Pill, useLoad } from "../components/common";
import {
  borrowerHref,
  DirectoryNote,
  LoanLink,
  LoanState,
  MoneyInTable,
  NotLive,
} from "../components/records";
import { fullDay, naira, phone } from "../format";
import { useSession } from "../session";

const PAGE = 50;

const VIEWS: { id: BorrowerView; label: string }[] = [
  { id: "all", label: "All" },
  { id: "arrears", label: "In arrears" },
  { id: "held", label: "Payments not yet posted" },
];

/** A search box for a list page. The search lives in the address, so it can be shared and reloaded. */
export function ListSearch({ q, onSearch, label }: { q: string; onSearch: (q: string) => void; label: string }) {
  const [text, setText] = useState(q);
  useEffect(() => setText(q), [q]);
  function submit(e: FormEvent) {
    e.preventDefault();
    onSearch(text.trim());
  }
  return (
    <form role="search" className="search-form" onSubmit={submit}>
      <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder={label} aria-label={label} />
      <button className="btn" type="submit">
        Search
      </button>
    </form>
  );
}

export function useListParams<V extends string>(key: string, fallback: V) {
  const [params, setParams] = useSearchParams();
  const view = (params.get(key) as V) || fallback;
  const q = params.get("q") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  function go(next: { view?: V; q?: string; offset?: number }) {
    const p = new URLSearchParams();
    const v = next.view ?? view;
    const text = next.q ?? q;
    if (v !== fallback) p.set(key, v);
    if (text) p.set("q", text);
    if (next.offset) p.set("offset", String(next.offset));
    setParams(p);
  }
  return { view, q, offset, go };
}

export function Borrowers() {
  const navigate = useNavigate();
  const { view, q, offset, go } = useListParams<BorrowerView>("view", "all");
  const { data, error, loading, reload } = useLoad(() => api.borrowers({ view, q, limit: PAGE, offset }), [view, q, offset]);

  return (
    <>
      <div className="head">
        <div>
          <h1>Borrowers</h1>
          <p className="sub">
            From the borrower directory, a copy of core banking for finding people quickly. A borrower's own page reads
            Fineract live. {data && <DirectoryNote directory={data.directory} />}
          </p>
        </div>
      </div>

      <div className="filters" role="group" aria-label="Which borrowers">
        {VIEWS.map((v) => (
          <button key={v.id} className="chip" aria-pressed={view === v.id} onClick={() => go({ view: v.id, offset: 0 })}>
            {v.label}
            {data && ` ${data.counts[v.id].toLocaleString()}`}
          </button>
        ))}
        <ListSearch q={q} onSearch={(text) => go({ q: text, offset: 0 })} label="Name, trading name, phone, customer or loan ref" />
      </div>

      <section className="panel">
        {error ? (
          <Failed error={error} retry={reload} />
        ) : !data ? (
          <Loading what="borrowers" />
        ) : data.items.length === 0 ? (
          <div className="empty">No borrowers match.</div>
        ) : (
          <>
            <div className="tablewrap" aria-busy={loading}>
              <table>
                <thead>
                  <tr>
                    <th>Borrower</th>
                    <th>Branch</th>
                    <th>Trading name</th>
                    <th>Loans</th>
                    <th className="num">In arrears</th>
                    <th>Not yet posted</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((b) => (
                    <tr key={b.ref} className="click" onClick={() => navigate(borrowerHref(b.ref))}>
                      <td>
                        <Link to={borrowerHref(b.ref)} onClick={(e) => e.stopPropagation()}>
                          <b>{b.name}</b>
                        </Link>
                        <div className="mono note">{b.ref}</div>
                      </td>
                      <td>{b.branch_name ?? "–"}</td>
                      <td>{b.business_name ?? "–"}</td>
                      <td>
                        {b.loan_refs.length
                          ? b.loan_refs.map((r, i) => (
                              <span key={r}>
                                {i > 0 && ", "}
                                <LoanLink loanRef={r} />
                              </span>
                            ))
                          : "–"}
                      </td>
                      <td className="num">
                        {b.arrears_kobo > 0 ? <Pill status="late" label={naira(b.arrears_kobo)} /> : "–"}
                      </td>
                      <td>
                        {b.held.lines > 0 ? (
                          <Pill
                            status="proposed"
                            label={`${naira(b.held.amount_kobo)}${b.held.lines > 1 ? ` · ${b.held.lines} payments` : ""}`}
                          />
                        ) : (
                          "–"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager offset={offset} limit={PAGE} total={data.total} onPage={(o) => go({ offset: o })} />
          </>
        )}
      </section>
    </>
  );
}

export function BorrowerDetail() {
  const { ref = "" } = useParams();
  const { config } = useSession();
  const { data, error, reload } = useLoad(() => api.borrower(ref), [ref]);

  if (error) return <Failed error={error} retry={reload} />;
  if (!data) return <Loading what="the borrower" />;
  const b = data;

  return (
    <>
      <div className="crumbs">
        <Link to="/borrowers">Borrowers</Link>
        <span>/</span>
        <span>{b.name}</span>
      </div>
      <div className="head">
        <div>
          <h1>{b.name}</h1>
          <p className="sub">
            <span className="mono">{b.ref}</span> · {b.branch_name ?? b.branch_code ?? "branch unknown"}
            {b.joined_on && ` · customer since ${fullDay(b.joined_on)}`}
            {!b.active && " · inactive"}
          </p>
        </div>
        {config?.links.fineract_web && (
          <ExternalLink href={`${config.links.fineract_web}/#/clients/${b.core_id}/general`}>
            Open in Fineract's staff screens
          </ExternalLink>
        )}
      </div>
      {!b.live && <NotLive copiedAt={b.copied_at} />}

      <div className="grid3">
        <section className="panel">
          <header>
            <h2>Profile</h2>
          </header>
          <div className="body">
            <dl className="kv">
              <dt>Phone</dt>
              <dd className="mono">{phone(b.phone)}</dd>
              <dt>Trading name</dt>
              <dd>{b.business_name ?? "–"}</dd>
              <dt>Group</dt>
              <dd>{b.group_name ?? "–"}</dd>
              <dt>Branch</dt>
              <dd>{b.branch_name ?? "–"}</dd>
            </dl>
          </div>
        </section>

        <section className="panel">
          <header>
            <h2>Loans</h2>
          </header>
          <div className="body">
            {b.loans.length === 0 ? (
              <p className="note">No loans.</p>
            ) : (
              b.loans.map((l) => (
                <div key={l.ref}>
                  <div className="loan-row">
                    <LoanLink loanRef={l.ref} />
                    <LoanState status={l.status} daysLate={l.days_late} />
                  </div>
                  <p className="note">
                    {l.product_name} · {naira(l.outstanding_kobo)} outstanding
                    {l.arrears_kobo > 0 && `, ${naira(l.arrears_kobo)} in arrears`}
                  </p>
                </div>
              ))
            )}
          </div>
        </section>

        <section className="panel">
          <header>
            <h2>Might be theirs</h2>
          </header>
          <div className="body">
            <p className="note">
              Payments in suspense whose payer or amount resembles this borrower. The investigator agent finds these in
              milestone 3.
            </p>
          </div>
        </section>
      </div>

      <section className="panel">
        <header>
          <h2>Payments received, every channel</h2>
          <span className="note">posted in Fineract, and payments Repaydesk holds that aren't posted yet</span>
        </header>
        <MoneyInTable rows={b.payments} showLoan={b.loans.length > 1} />
      </section>

      <div className="later-box">Milestone 4 adds collections here: messages drafted and sent, and promises to pay.</div>
    </>
  );
}
