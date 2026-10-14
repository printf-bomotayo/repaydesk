import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ApiError, type CollectionsMessage, type MessageStatus, type Reply } from "../api";
import { announceChange, Failed, Loading, Pager, Pill, useLoad, useToast } from "../components/common";
import { borrowerHref, LoanLink } from "../components/records";
import { bankToday, day, naira, phone, stamp } from "../format";
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

/** Collections messages: the drafts a portfolio manager approves, edits or rejects, what happened to each since, and the borrowers' replies. */
export function Messages() {
  const { staff } = useSession();
  const [params, setParams] = useSearchParams();
  const replying = params.get("view") === "replies";
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
            08:00 and 18:00, Monday to Saturday. A reply with a date becomes a promise to pay; any other reply waits here
            for a person.
          </p>
        </div>
      </section>

      <div className="filters" role="group" aria-label="Messages by status">
        {TABS.map(([s, label]) => (
          <button
            key={s}
            className="chip"
            aria-pressed={!replying && status === s}
            onClick={() => set({ status: s === "draft" ? "" : s, offset: "", view: "", show: "" })}
          >
            {label}
            {data && ` ${data.counts[s].toLocaleString()}`}
          </button>
        ))}
        <button className="chip" aria-pressed={replying} onClick={() => set({ view: "replies", status: "", offset: "" })}>
          Replies
        </button>
      </div>

      {replying ? (
        <Replies decides={decides} params={params} set={set} />
      ) : error ? (
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

// ---- replies ---------------------------------------------------------------------------------------

const OUTCOME: Record<string, { label: string; pill: string }> = {
  promise: { label: "a promise to pay", pill: "approved" },
  says_paid: { label: "says it has paid", pill: "suspense" },
  question: { label: "a question", pill: "proposed" },
  stop: { label: "asks to stop", pill: "late" },
  unclear: { label: "unclear", pill: "new" },
  unknown_number: { label: "an unknown number", pill: "new" },
};
const PROMISE_PILL: Record<string, string> = { open: "approved", kept: "posted", broken: "late", cancelled: "superseded" };
const SHOW: [string, string][] = [
  ["", "Waiting for a person"],
  ["done", "Dealt with"],
  ["all", "All"],
];

function Replies({ decides, params, set }: { decides: boolean; params: URLSearchParams; set: (c: Record<string, string>) => void }) {
  const show = params.get("show") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const waiting = show === "" ? true : show === "done" ? false : undefined;
  const { data, error, reload } = useLoad(() => api.replies({ waiting, limit: PAGE, offset }), [show, offset]);

  return (
    <>
      <div className="filters" role="group" aria-label="Replies">
        {SHOW.map(([k, label]) => (
          <button key={k || "waiting"} className="chip" aria-pressed={show === k} onClick={() => set({ show: k, offset: "" })}>
            {label}
            {k === "" && data && ` ${data.waiting.toLocaleString()}`}
          </button>
        ))}
      </div>
      {error ? (
        <Failed error={error} retry={reload} />
      ) : !data ? (
        <Loading what="the replies" />
      ) : (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {data.items.map((r) => (
              <ReplyCard key={r.id} reply={r} decides={decides} onDone={reload} />
            ))}
          </div>
          {data.items.length === 0 && <p className="empty">{show === "" ? "No replies waiting for a person." : "None."}</p>}
          <Pager offset={offset} limit={PAGE} total={data.total} onPage={(o) => set({ offset: o ? String(o) : "" })} />
        </>
      )}
    </>
  );
}

function ReplyCard({ reply: r, decides, onDone }: { reply: Reply; decides: boolean; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [due, setDue] = useState("");
  const [amount, setAmount] = useState(r.asked_kobo ? String(r.asked_kobo / 100) : "");
  const [note, setNote] = useState("");
  const o = r.outcome ? OUTCOME[r.outcome] : null;
  const who = r.borrower_name ?? phone(r.from_phone);

  async function act(action: () => Promise<string>) {
    setBusy(true);
    setRefusal(null);
    try {
      toast(await action());
      onDone();
      announceChange();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) setRefusal(e.message);
      else {
        toast(
          e instanceof ApiError && e.status === 409
            ? "Someone has already dealt with this reply."
            : e instanceof Error
              ? e.message
              : "That didn't work.",
        );
        onDone();
      }
    } finally {
      setBusy(false);
    }
  }

  function promise(e: FormEvent) {
    e.preventDefault();
    const kobo = Math.round(Number(amount.replace(/,/g, "")) * 100);
    act(async () => {
      await api.promiseFromReply(r.id, { due_on: due, amount_kobo: kobo });
      return `Promise recorded: ${naira(kobo)} by ${day(due)}. ${who} won't be messaged before then.`;
    });
  }

  function close(e: FormEvent) {
    e.preventDefault();
    act(async () => {
      await api.closeReply(r.id, note.trim());
      return "Dealt with. Your note is on the reply.";
    });
  }

  return (
    <article className="queue-item" aria-label={`Reply from ${who}`}>
      <div>
        <span className="ch">
          {CHANNEL[r.channel] ?? r.channel} · {stamp(r.received_at)}
        </span>
        {r.borrower_ref ? <Link to={borrowerHref(r.borrower_ref)}>{who}</Link> : <b>{who}</b>}
        <span className="note mono">{phone(r.from_phone)}</span>
        {r.loan_ref && <LoanLink loanRef={r.loan_ref} />}
        {r.answers && (
          <span className="note">
            Answering: <i>{r.answers}</i>
          </span>
        )}
      </div>
      <div>
        <p style={{ margin: 0, fontSize: 18, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>"{r.body}"</p>
        <span className="ch">
          {o && <Pill status={o.pill} label={o.label} />}
          {r.reading?.why && ` · ${r.reading.why}`}
        </span>
        {r.promise && (
          <span>
            Promise: <b>{naira(r.promise.amount_kobo)}</b> by {day(r.promise.due_on)} ·{" "}
            <Pill status={PROMISE_PILL[r.promise.status] ?? "new"} label={r.promise.status} />
          </span>
        )}
        {r.handled_at && (
          <span className="note">
            {r.handled_by === "rule:replies" ? "Recorded from the reply" : `Dealt with by ${r.handled_by_name ?? r.handled_by}`},{" "}
            {stamp(r.handled_at)}
            {r.note && `: ${r.note}`}
          </span>
        )}
        {refusal && (
          <div className="check no" role="alert">
            <span className="m">✕</span>
            <span>{refusal}</span>
          </div>
        )}
      </div>
      {!r.handled_at && (
        <footer>
          {!decides ? (
            <span className="note">Read only: portfolio managers and supervisors deal with replies.</span>
          ) : (
            <>
              <form className="reject-form" onSubmit={promise} aria-label="Record a promise">
                <label htmlFor={`due-${r.id}`}>Promised for</label>
                <input id={`due-${r.id}`} type="date" min={bankToday()} value={due} onChange={(e) => setDue(e.target.value)} required />
                <label htmlFor={`amt-${r.id}`}>Amount, ₦</label>
                <input
                  id={`amt-${r.id}`}
                  inputMode="decimal"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  required
                  style={{ width: 110 }}
                />
                <button className="btn primary" type="submit" disabled={busy || !due || !amount || !r.loan_ref}>
                  Record promise
                </button>
              </form>
              <form className="reject-form" onSubmit={close} aria-label="Close the reply">
                <input aria-label="What was done" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="What was done" />
                <button className="btn" type="submit" disabled={busy || !note.trim()}>
                  Close
                </button>
              </form>
            </>
          )}
        </footer>
      )}
    </article>
  );
}
