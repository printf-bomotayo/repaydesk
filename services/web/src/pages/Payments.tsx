import { Link, useNavigate, useSearchParams } from "react-router";
import { api, CHANNELS, STATUSES } from "../api";
import { Failed, Loading, Pager, Pill, useLoad } from "../components/common";
import { actorLabel, CHANNEL_LABEL, day, naira, STATUS_LABEL } from "../format";

const PAGE = 50;

export function lineHref(id: string): string {
  return `/payments/${encodeURIComponent(id)}`;
}

export function Payments() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const status = params.get("status") ?? "";
  const channel = params.get("channel") ?? "";
  const q = params.get("q") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;

  const { data, error, loading, reload } = useLoad(
    () => api.lines({ status, channel, q, limit: PAGE, offset }),
    [status, channel, q, offset],
  );

  function set(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    if (!("offset" in changes)) next.delete("offset");
    setParams(next);
  }

  // Copies of lines already held are their own filter, not part of "All".
  const all = data ? Object.values(data.counts).reduce((a, b) => a + b, 0) - data.counts.duplicate : null;

  return (
    <>
      <div className="head">
        <div>
          <h1>Payments</h1>
          <p className="sub">
            Every line that arrived, from every channel, once. A line that arrived again is kept as a duplicate,
            pointing at its original, under its own filter.
          </p>
        </div>
      </div>

      <div className="filters" role="group" aria-label="Filter by status">
        <button className="chip" aria-pressed={!status} onClick={() => set({ status: "" })}>
          All{all !== null && ` ${all.toLocaleString()}`}
        </button>
        {STATUSES.map((s) => (
          <button key={s} className="chip" aria-pressed={status === s} onClick={() => set({ status: s })}>
            {STATUS_LABEL[s]}
            {data && ` ${data.counts[s].toLocaleString()}`}
          </button>
        ))}
        <select aria-label="Channel" value={channel} onChange={(e) => set({ channel: e.target.value })}>
          <option value="">Every channel</option>
          {CHANNELS.map((c) => (
            <option key={c} value={c}>
              {CHANNEL_LABEL[c]}
            </option>
          ))}
        </select>
        {q && (
          <button className="chip" aria-pressed="true" onClick={() => set({ q: "" })} title="Clear the search">
            “{q}” ✕
          </button>
        )}
      </div>

      <section className="panel">
        {error ? (
          <Failed error={error} retry={reload} />
        ) : !data ? (
          <Loading what="payments" />
        ) : data.items.length === 0 ? (
          <div className="empty">No payments match.</div>
        ) : (
          <>
            <div className="tablewrap" aria-busy={loading}>
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Channel</th>
                    <th>Narration</th>
                    <th className="num">Amount</th>
                    <th>Status</th>
                    <th>Proposal</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((l) => (
                    <tr key={l.id} className="click" onClick={() => navigate(lineHref(l.id))}>
                      <td className="mono">{day(l.value_date)}</td>
                      <td className="ch">{CHANNEL_LABEL[l.channel] ?? l.channel}</td>
                      <td>
                        <Link to={lineHref(l.id)} className="narr" onClick={(e) => e.stopPropagation()}>
                          {l.narration}
                        </Link>
                        {l.payer_name && <div className="note">{l.payer_name}</div>}
                      </td>
                      <td className="num">{naira(l.amount_kobo)}</td>
                      <td>
                        <Pill status={l.status} />
                      </td>
                      <td>
                        {l.duplicate_of ? (
                          <span className="note">copy of an earlier line</span>
                        ) : l.proposal_kind === "exclude" ? (
                          <span className="note">not a repayment · {actorLabel(l.proposed_by ?? "")}</span>
                        ) : l.loan_refs.length ? (
                          <>
                            <span className="mono">{l.loan_refs.join(", ")}</span>
                            <div className="note">{actorLabel(l.proposed_by ?? "")}</div>
                          </>
                        ) : (
                          <span className="note">–</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager offset={offset} limit={PAGE} total={data.total} onPage={(o) => set({ offset: String(o) })} />
          </>
        )}
      </section>
    </>
  );
}
