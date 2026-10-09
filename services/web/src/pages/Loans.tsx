import { Link, useNavigate, useParams } from "react-router";
import { api, type LoanBand, type LoanRecord } from "../api";
import { ExternalLink, Failed, Loading, Pager, Pill, useLoad } from "../components/common";
import { borrowerHref, DirectoryNote, LoanLink, NotLive, Reference, loanHref } from "../components/records";
import { bankToday, CHANNEL_LABEL, day, fullDay, naira } from "../format";
import { useSession } from "../session";
import { ListSearch, useListParams } from "./Borrowers";

const PAGE = 50;

const BANDS: { id: LoanBand; label: string }[] = [
  { id: "all", label: "All" },
  { id: "late_1_7", label: "1 to 7 days late" },
  { id: "late_8_30", label: "8 to 30" },
  { id: "late_31_90", label: "31 to 90" },
  { id: "late_90", label: "Over 90" },
  { id: "closed", label: "Closed" },
];

export function Loans() {
  const navigate = useNavigate();
  const { view: band, q, offset, go } = useListParams<LoanBand>("band", "all");
  const { data, error, loading, reload } = useLoad(() => api.loans({ band, q, limit: PAGE, offset }), [band, q, offset]);

  return (
    <>
      <div className="head">
        <div>
          <h1>Loans</h1>
          <p className="sub">
            {data && (
              <>
                {data.counts.all.toLocaleString()} loans · {data.counts.in_arrears.toLocaleString()} in arrears. Most
                overdue first.{" "}
              </>
            )}
            {data && <DirectoryNote directory={data.directory} />}
          </p>
        </div>
      </div>

      <div className="filters" role="group" aria-label="How late">
        {BANDS.map((b) => (
          <button key={b.id} className="chip" aria-pressed={band === b.id} onClick={() => go({ view: b.id, offset: 0 })}>
            {b.label}
            {data && ` ${data.counts[b.id].toLocaleString()}`}
          </button>
        ))}
        <ListSearch q={q} onSearch={(text) => go({ q: text, offset: 0 })} label="Loan ref, borrower or customer ref" />
      </div>

      <section className="panel">
        {error ? (
          <Failed error={error} retry={reload} />
        ) : !data ? (
          <Loading what="loans" />
        ) : data.items.length === 0 ? (
          <div className="empty">No loans match.</div>
        ) : (
          <>
            <div className="tablewrap" aria-busy={loading}>
              <table>
                <thead>
                  <tr>
                    <th>Loan</th>
                    <th>Borrower</th>
                    <th>Product</th>
                    <th className="num">Outstanding</th>
                    <th className="num">Arrears</th>
                    <th className="num">Days late</th>
                    <th className="num">Not yet posted</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((l) => (
                    <tr key={l.ref} className="click" onClick={() => navigate(loanHref(l.ref))}>
                      <td>
                        <LoanLink loanRef={l.ref} />
                        {l.status !== "active" && <div className="note">{l.status}</div>}
                      </td>
                      <td>{l.borrower_name ?? l.borrower_ref}</td>
                      <td>{l.product_name}</td>
                      <td className="num">{naira(l.outstanding_kobo)}</td>
                      <td className="num">{l.arrears_kobo > 0 ? naira(l.arrears_kobo) : "–"}</td>
                      <td className="num">{l.days_late || "–"}</td>
                      <td className="num">{l.held_kobo > 0 ? <Pill status="proposed" label={naira(l.held_kobo)} /> : "–"}</td>
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

export function LoanDetail() {
  const { ref = "" } = useParams();
  const { config } = useSession();
  const { data, error, reload } = useLoad(() => api.loan(ref), [ref]);

  if (error) return <Failed error={error} retry={reload} />;
  if (!data) return <Loading what="the loan" />;
  const l = data;
  const fineract = config?.links.fineract_web && l.borrower_core_id
    ? `${config.links.fineract_web}/#/clients/${l.borrower_core_id}/loans-accounts/${l.core_id}/general`
    : null;

  return (
    <>
      <div className="crumbs">
        <Link to="/loans">Loans</Link>
        <span>/</span>
        <span className="mono">{l.ref}</span>
      </div>
      <div className="head">
        <div>
          <h1>
            <span className="mono" style={{ fontSize: 19 }}>
              {l.ref}
            </span>
          </h1>
          <p className="sub">
            <Link to={borrowerHref(l.borrower_ref)}>{l.borrower_name ?? l.borrower_ref}</Link> · {l.product_name}
            {l.disbursed_on && ` · disbursed ${fullDay(l.disbursed_on)}`}
            {l.status !== "active" && ` · ${l.status}`}
          </p>
        </div>
        {l.arrears_kobo > 0 && (
          <Pill status="late" label={`${naira(l.arrears_kobo)} in arrears · ${l.days_late} day${l.days_late === 1 ? "" : "s"}`} />
        )}
      </div>
      {!l.live && <NotLive copiedAt={l.copied_at} />}

      <section className="stats">
        <div className="panel stat">
          <span className="k">Principal</span>
          <span className="v">{naira(l.principal_kobo)}</span>
        </div>
        <div className="panel stat">
          <span className="k">Outstanding</span>
          <span className="v">{naira(l.outstanding_kobo)}</span>
          <span className="d">{l.live ? "per Fineract, live" : "the directory's copy"}</span>
        </div>
        <div className="panel stat">
          <span className="k">If held payments post</span>
          <span className="v">{naira(Math.max(l.outstanding_kobo - l.held_kobo, 0))}</span>
          <span className="d">
            {l.held_kobo > 0 ? `${naira(l.held_kobo)} held by Repaydesk, not yet posted` : "nothing held"}
          </span>
        </div>
        <div className="panel stat">
          <span className="k">Virtual account</span>
          <span className="v mono" style={{ fontSize: 17 }}>
            {l.virtual_account?.number ?? "–"}
          </span>
          <span className="d">{l.virtual_account ? providerName(l.virtual_account.provider) : "none issued"}</span>
        </div>
      </section>

      <div className="split">
        <section className="panel">
          <header>
            <h2>Schedule</h2>
            {fineract && <ExternalLink href={fineract}>Fineract's staff screens</ExternalLink>}
          </header>
          {l.schedule ? <Schedule loan={l} /> : <div className="empty">Fineract didn't answer; try again shortly.</div>}
        </section>

        <section className="panel">
          <header>
            <h2>Money in, one timeline</h2>
          </header>
          <div className="body">
            {l.money_in.length === 0 ? (
              <p className="note">Nothing paid yet.</p>
            ) : (
              <ol className="timeline">
                {l.money_in.map((p) => (
                  <li key={`${p.reference}-${p.status}`} className={p.status === "posted" ? "" : "pending"}>
                    <span className="t">{p.on ? day(p.on) : "–"}</span>
                    <span className="dot" />
                    <span>
                      {naira(p.amount_kobo)} · {p.channel ? (CHANNEL_LABEL[p.channel] ?? p.channel) : "channel not recorded"} ·{" "}
                      <Pill status={p.status} />
                      <br />
                      <Reference p={p} />
                    </span>
                  </li>
                ))}
              </ol>
            )}
            <p className="note">
              Posted repayments come from Fineract. Hollow dots are payments Repaydesk holds that aren't posted yet, so a
              borrower who paid is never shown as unpaid while a reviewer catches up.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}

function providerName(p: string): string {
  return p === "payrail" ? "PayRail" : p;
}

/** The instalments, each marked paid, part paid, late or upcoming as of today at the bank. */
function Schedule({ loan }: { loan: LoanRecord }) {
  const today = bankToday();
  let heldLeft = loan.held_kobo;
  return (
    <div className="tablewrap">
      <table className="sched">
        <thead>
          <tr>
            <th>#</th>
            <th>Due</th>
            <th className="num">Due amount</th>
            <th className="num">Paid</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(loan.schedule ?? []).map((s) => {
            const owed = s.due_kobo - s.paid_kobo;
            let note = "";
            let late = false;
            if (owed <= 0) note = "paid";
            else if (s.due_on < today) {
              late = true;
              // Held payments go to the oldest late instalments first, as Fineract will apply them.
              const covers = heldLeft > 0;
              heldLeft -= owed;
              note = covers ? "late · payment held" : "late";
            } else if (s.paid_kobo > 0) note = "part paid";
            return (
              <tr key={s.number}>
                <td>{s.number}</td>
                <td className="mono">{fullDay(s.due_on)}</td>
                <td className="num">{naira(s.due_kobo)}</td>
                <td className="num">{s.paid_kobo ? naira(s.paid_kobo) : "–"}</td>
                <td className={late ? "late" : "note"}>{note}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
