import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ApiError, type CollectionsMessage, type MessageStatus } from "../api";
import { announceChange, Failed, Loading, Pager, Pill, useLoad, useToast } from "../components/common";
import { borrowerHref, LoanLink } from "../components/records";
import { naira, stamp } from "../format";
import { useSession } from "../session";
import { RejectForm } from "./Review";
import { runHref } from "./Runs";

const PAGE = 20;

const TABS: [MessageStatus, string][] = [
  ["draft", "Waiting for approval"],
  ["approved", "Approved, to send"],
  ["sent", "Sent"],
  ["failed", "Failed"],
  ["rejected", "Rejected"],
];
const STATUS_PILL: Record<MessageStatus, string> = {
  draft: "draft",
  approved: "approved, to send",
  sent: "sent",
  failed: "failed",
  rejected: "rejected",
};
const CHANNEL: Record<string, string> = { sms: "SMS", whatsapp: "WhatsApp" };
const LIMIT: Record<string, number> = { sms: 320, whatsapp: 600 };
const KIND_PILL: Record<string, string> = { reminder: "suspense", follow_up: "proposed", arrangement: "two", broken_promise: "late" };
const REASONS = ["Tone is wrong for this borrower", "The borrower has already been in touch", "Not the right time to message", "Wrong details"];

/** Collections messages: the drafts a portfolio manager approves, edits or rejects, and what happened to each since. */
export function Messages() {
  const { staff } = useSession();
  const [params, setParams] = useSearchParams();
  const status = (params.get("status") as MessageStatus | null) ?? "draft";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const { data, error, reload } = useLoad(() => api.messages({ status, limit: PAGE, offset }), [status, offset]);
  const decides = staff?.role === "manager" || staff?.role === "supervisor";

  function set(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next);
  }

  return (
    <>
      <section className="head">
        <div>
          <span className="eyebrow">Collections</span>
          <h1>Messages</h1>
          <p className="sub">
            The drafting agent writes; a portfolio manager decides. Each message is checked against the collections policy
            again when it is approved, against the loan as core banking shows it then. Approved messages go out between
            08:00 and 18:00, Monday to Saturday.
          </p>
        </div>
      </section>

      <div className="filters" role="group" aria-label="Messages by status">
        {TABS.map(([s, label]) => (
          <button key={s} className="chip" aria-pressed={status === s} onClick={() => set({ status: s === "draft" ? "" : s, offset: "" })}>
            {label}
            {data && ` ${data.counts[s].toLocaleString()}`}
          </button>
        ))}
      </div>

      {error ? (
        <Failed error={error} retry={reload} />
      ) : !data ? (
        <Loading what="the messages" />
      ) : (
        <>
          {status === "draft" && !decides && (
            <p className="note">Portfolio managers and supervisors approve messages. You can read them here.</p>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {data.items.map((m) =>
              m.status === "draft" ? (
                <Draft key={m.id} message={m} decides={decides} onDecided={reload} />
              ) : (
                <Decided key={m.id} message={m} />
              ),
            )}
          </div>
          {data.items.length === 0 && (
            <p className="empty">
              {status === "draft" ? (
                <>
                  No drafts waiting. <span className="mono">python scripts/dev.py draft</span> drafts the morning's messages.
                </>
              ) : (
                "None."
              )}
            </p>
          )}
          <Pager offset={offset} limit={PAGE} total={data.total} onPage={(o) => set({ offset: o ? String(o) : "" })} />
        </>
      )}
    </>
  );
}

function Who({ m }: { m: CollectionsMessage }) {
  return (
    <div>
      <span className="ch">
        <Pill status={KIND_PILL[m.bucket] ?? "new"} label={m.bucket_label} /> {CHANNEL[m.channel] ?? m.channel}
        {m.days_late !== null && ` · ${m.days_late} days late`}
      </span>
      <span className="big">{naira(m.amount_due_kobo)}</span>
      <Link to={borrowerHref(m.borrower_ref)}>{m.borrower_name ?? m.borrower_ref}</Link>
      <span className="note">{m.branch_name}</span>
      <LoanLink loanRef={m.loan_ref} />
      {m.run_id !== null && (
        <Link to={runHref(m.run_id)} className="note">
          How it was drafted (run {m.run_id})
        </Link>
      )}
    </div>
  );
}

function Checks({ checks }: { checks: CollectionsMessage["checks"] }) {
  const failed = checks.filter((c) => !c.ok);
  if (checks.length === 0) return null;
  return failed.length === 0 ? (
    <span className="note">Passes all {checks.length} of the policy's checks.</span>
  ) : (
    <div aria-label="Failed checks">
      {failed.map((c) => (
        <div key={c.check} className="check no">
          <span className="m" aria-label="failed">
            ✕
          </span>
          <span>{c.detail}</span>
        </div>
      ))}
    </div>
  );
}

function Draft({ message: m, decides, onDecided }: { message: CollectionsMessage; decides: boolean; onDecided: () => void }) {
  const toast = useToast();
  const [body, setBody] = useState(m.body);
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const edited = body.trim() !== m.body;
  const limit = LIMIT[m.channel] ?? 320;

  async function decide(action: () => Promise<string>) {
    setBusy(true);
    setRefusal(null);
    try {
      toast(await action());
      onDecided();
      announceChange();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) setRefusal(e.message); // the policy said no: fix and try again
      else {
        toast(
          e instanceof ApiError && e.status === 409
            ? "Someone decided this one while you were looking. The list has been refreshed."
            : e instanceof Error
              ? e.message
              : "That didn't work.",
        );
        onDecided();
      }
    } finally {
      setBusy(false);
    }
  }

  const approve = () =>
    decide(async () => {
      const res = await api.approveMessage(m.id, edited ? body.trim() : undefined);
      return `Approved${res.edited ? " as edited" : ""}. It goes to ${m.borrower_name ?? m.borrower_ref} from ${stamp(res.send_after)}.`;
    });

  const reject = (reason: string) =>
    decide(async () => {
      await api.rejectMessage(m.id, reason);
      return "Rejected. The loan goes back on the morning list.";
    });

  return (
    <article className={`queue-item${m.drafted_by.startsWith("agent:") ? " agent" : ""}`} aria-label={`Message to ${m.borrower_name ?? m.borrower_ref}`}>
      <Who m={m} />
      <div>
        <span className="ch">
          {m.drafted_by === "agent:collector" ? "Drafted by the agent" : `Drafted by ${m.drafted_by}`} · {stamp(m.created_at)}
        </span>
        {decides ? (
          <>
            <textarea
              aria-label="The message"
              value={body}
              rows={5}
              onChange={(e) => setBody(e.target.value)}
              style={{ width: "100%", background: "var(--canvas)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "10px 12px", resize: "vertical", lineHeight: 1.45 }}
            />
            <span className="note" aria-live="polite">
              {body.trim().length} of {limit} characters{edited && " · edited"}
            </span>
          </>
        ) : (
          <p style={{ margin: 0, fontSize: 17, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>{m.body}</p>
        )}
        {m.note && <span className="note">From the agent: {m.note}</span>}
        {!edited && <Checks checks={m.checks} />}
        {refusal && (
          <div className="check no" role="alert">
            <span className="m">✕</span>
            <span>{refusal}</span>
          </div>
        )}
      </div>
      <footer>
        <span className="note">
          {m.passes_policy || edited ? "Checked again against the loan when you approve." : "Fix the failed checks before approving."}
        </span>
        {!decides ? (
          <span className="note">Read only</span>
        ) : rejecting ? (
          <RejectForm id={m.id} busy={busy} onReject={reject} onCancel={() => setRejecting(false)} reasons={REASONS} />
        ) : (
          <div className="actions">
            {edited && (
              <button className="btn" disabled={busy} onClick={() => setBody(m.body)}>
                Undo edits
              </button>
            )}
            <button className="btn" disabled={busy} onClick={() => setRejecting(true)}>
              Reject
            </button>
            <button className="btn primary" disabled={busy || !body.trim() || (!edited && !m.passes_policy)} onClick={approve}>
              {busy ? "Saving…" : edited ? "Approve as edited" : "Approve"}
            </button>
          </div>
        )}
      </footer>
    </article>
  );
}

function Decided({ message: m }: { message: CollectionsMessage }) {
  return (
    <article className="queue-item" aria-label={`Message to ${m.borrower_name ?? m.borrower_ref}`}>
      <Who m={m} />
      <div>
        <span className="ch">
          <Pill status={m.status === "approved" ? "approved" : m.status} label={STATUS_PILL[m.status]} />
          {m.decided_by_name && ` by ${m.decided_by_name}`}
          {m.decided_at && `, ${stamp(m.decided_at)}`}
        </span>
        <p style={{ margin: 0, fontSize: 17, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>{m.body}</p>
        {m.drafted_body && (
          <details>
            <summary className="note" style={{ cursor: "pointer" }}>
              Edited before approval: what the agent wrote
            </summary>
            <p className="note" style={{ whiteSpace: "pre-wrap" }}>
              {m.drafted_body}
            </p>
          </details>
        )}
        {m.status === "approved" && m.send_after && (
          <span className="note">
            {m.error ? `Retrying after: ${m.error}. ` : ""}Goes out from {stamp(m.send_after)}.
          </span>
        )}
        {m.status === "sent" && m.sent_at && (
          <span className="note">
            Sent {stamp(m.sent_at)} · provider id <span className="mono">{m.provider_id}</span>
          </span>
        )}
        {m.status === "failed" && m.error && (
          <div className="check no">
            <span className="m">✕</span>
            <span>{m.error}</span>
          </div>
        )}
        {m.status === "rejected" && m.reject_reason && <span className="note">Why: {m.reject_reason}</span>}
      </div>
    </article>
  );
}
