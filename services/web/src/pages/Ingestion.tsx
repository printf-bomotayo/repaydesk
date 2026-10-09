import { Link } from "react-router";
import { api } from "../api";
import { Failed, Loading, Pill, useLoad } from "../components/common";
import { stamp } from "../format";
import { lineHref } from "./Payments";

const SOURCE: Record<string, string> = {
  northgate: "Northgate Bank · CSV",
  coastline: "Coastline Bank · MT940",
  termpay: "TermPay · POS settlement",
  "branch-cash": "Branch teller sheets",
};

const OUTCOME_PILL: Record<string, string> = { stored: "approved", duplicate: "duplicate", ignored: "new", rejected: "rejected" };

export function Ingestion() {
  const { data, error, reload } = useLoad(() => api.ingestion(), []);

  return (
    <>
      <div className="head">
        <div>
          <h1>Ingestion</h1>
          <p className="sub">
            What arrived and how it was read. Every file's lines add up: stored plus duplicates equals the lines in the
            file.
          </p>
        </div>
      </div>
      {error ? (
        <Failed error={error} retry={reload} />
      ) : !data ? (
        <Loading what="ingestion" />
      ) : (
        <>
          <div className="stats">
            {data.sources.map((s) => (
              <section key={s.source} className="panel stat">
                <span className="k">{SOURCE[s.source] ?? s.source}</span>
                <span className="v">{s.stored.toLocaleString()}</span>
                <span className="d">
                  lines from {s.files} file{s.files === 1 ? "" : "s"}
                  {s.duplicates > 0 && ` · ${s.duplicates} duplicates`}
                </span>
              </section>
            ))}
            <section className="panel stat">
              <span className="k">PayRail · webhooks</span>
              <span className="v">{data.webhooks.outcomes.stored.toLocaleString()}</span>
              <span className="d">
                stored · {data.webhooks.outcomes.duplicate} redelivered · {data.webhooks.outcomes.rejected} refused
              </span>
            </section>
          </div>

          <section className="panel">
            <header>
              <h2>Statement files</h2>
              <span className="note">newest first</span>
            </header>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Received</th>
                    <th>Source</th>
                    <th>File</th>
                    <th className="num">Lines</th>
                    <th className="num">Stored</th>
                    <th className="num">Duplicates</th>
                  </tr>
                </thead>
                <tbody>
                  {data.files.map((f) => (
                    <tr key={f.id}>
                      <td className="mono">{stamp(f.received_at)}</td>
                      <td className="ch">{SOURCE[f.source] ?? f.source}</td>
                      <td className="mono">{f.file_name}</td>
                      <td className="num">{f.line_count ?? "–"}</td>
                      <td className="num">{f.stored}</td>
                      <td className="num">{f.duplicates || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <header>
              <h2>Webhook deliveries</h2>
              <span className="note">the latest 50 · bodies are not kept, only their hash</span>
            </header>
            {data.webhooks.recent.length === 0 ? (
              <div className="empty">No deliveries yet.</div>
            ) : (
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>Received</th>
                      <th>Event</th>
                      <th>Signature</th>
                      <th>Outcome</th>
                      <th>Line</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.webhooks.recent.map((w) => (
                      <tr key={w.id}>
                        <td className="mono">{stamp(w.received_at)}</td>
                        <td className="mono">{w.event_id ?? "–"}</td>
                        <td>{w.signature_ok ? "valid" : <Pill status="rejected" label="forged or damaged" />}</td>
                        <td>
                          <Pill status={OUTCOME_PILL[w.outcome]} label={w.outcome} />
                        </td>
                        <td>
                          {w.line_id ? (
                            <Link to={lineHref(w.line_id)} className="mono">
                              {w.line_id}
                            </Link>
                          ) : (
                            "–"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
