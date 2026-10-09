import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type AuditRow } from "../api";
import { ExternalLink, Failed, Loading, useLoad } from "../components/common";
import { actorLabel, stamp } from "../format";
import { useSession } from "../session";
import { lineHref } from "./Payments";

const LIMIT = 100;

/** Newest first, paged by id: "Older" asks for rows before the last one shown, so rows arriving
 *  meanwhile never shift what's on screen. */
export function Audit() {
  const [params, setParams] = useSearchParams();
  const actor = params.get("actor") ?? "";
  const action = params.get("action") ?? "";
  const subject = params.get("subject") ?? "";
  const before = Number(params.get("before")) || null;
  const [actorDraft, setActorDraft] = useState(actor);
  const [subjectDraft, setSubjectDraft] = useState(subject);

  const { data, error, loading, reload } = useLoad(
    () => api.audit({ actor, action, subject, before, limit: LIMIT }),
    [actor, action, subject, before],
  );

  function set(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    if (!("before" in changes)) next.delete("before");
    setParams(next);
  }

  function onFilter(e: FormEvent) {
    e.preventDefault();
    set({ actor: actorDraft.trim(), subject: subjectDraft.trim() });
  }

  return (
    <>
      <div className="head">
        <div>
          <h1>Audit log</h1>
          <p className="sub">
            Every decision and every write, append-only: the database refuses to change or delete a row.
          </p>
        </div>
      </div>

      <form className="filters" onSubmit={onFilter} aria-label="Filter the audit log">
        <select aria-label="Action" value={action} onChange={(e) => set({ action: e.target.value })}>
          <option value="">Every action</option>
          {data?.actions.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <input aria-label="Who" placeholder="Who, e.g. amaka or rule:virtual_account" value={actorDraft} onChange={(e) => setActorDraft(e.target.value)} />
        <input aria-label="About" placeholder="About, e.g. a line id" value={subjectDraft} onChange={(e) => setSubjectDraft(e.target.value)} />
        <button className="btn" type="submit">
          Filter
        </button>
        {(actor || action || subject || before) && (
          <button
            className="linkbtn"
            type="button"
            onClick={() => {
              setActorDraft("");
              setSubjectDraft("");
              setParams(new URLSearchParams());
            }}
          >
            Clear
          </button>
        )}
      </form>

      <section className="panel">
        {error ? (
          <Failed error={error} retry={reload} />
        ) : !data ? (
          <Loading what="the audit log" />
        ) : data.items.length === 0 ? (
          <div className="empty">Nothing recorded matches.</div>
        ) : (
          <>
            <div className="tablewrap" aria-busy={loading}>
              <table>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Who</th>
                    <th>Action</th>
                    <th>About</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <AuditLine key={row.id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
            <nav className="pager" aria-label="Pages">
              <span>
                {before ? "Older entries" : "Newest entries"} · {data.items.length} shown
              </span>
              <span className="actions">
                {before && (
                  <button className="btn" onClick={() => set({ before: "" })}>
                    Newest
                  </button>
                )}
                <button className="btn" disabled={!data.next_before} onClick={() => set({ before: String(data.next_before) })}>
                  Older
                </button>
              </span>
            </nav>
          </>
        )}
      </section>
    </>
  );
}

function AuditLine({ row }: { row: AuditRow }) {
  const { config } = useSession();
  const lineId = row.subject.startsWith("payment_line:") ? row.subject.slice("payment_line:".length) : null;
  const detail = Object.keys(row.detail).length ? row.detail : null;
  return (
    <tr>
      <td className="mono">{stamp(row.at)}</td>
      <td>{row.actor_name ?? actorLabel(row.actor)}</td>
      <td className="mono">{row.action}</td>
      <td>
        {lineId ? (
          <Link to={lineHref(lineId)} className="mono">
            {lineId}
          </Link>
        ) : (
          <span className="mono">{row.subject}</span>
        )}
      </td>
      <td>
        {detail && (
          <details className="json">
            <summary>{summarise(detail)}</summary>
            <pre className="raw">{JSON.stringify(detail, null, 2)}</pre>
          </details>
        )}
        {row.trace_id && config && <ExternalLink href={`${config.links.jaeger}/trace/${row.trace_id}`}>trace</ExternalLink>}
      </td>
    </tr>
  );
}

function summarise(detail: Record<string, unknown>): string {
  if (typeof detail.reason === "string") return `“${detail.reason}”`;
  if (typeof detail.file === "string") return detail.file;
  if (typeof detail.kind === "string") return String(detail.kind);
  return "details";
}
