import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ApiError, type QueueItem, type QueueView } from "../api";
import { Checks } from "../components/Checks";
import { announceChange, Failed, Loading, Pager, useLoad, useToast } from "../components/common";
import { actorLabel, CHANNEL_LABEL, day, naira } from "../format";
import { useSession } from "../session";
import { lineHref } from "./Payments";

const PAGE = 25;

const VIEWS: { id: QueueView; label: string }[] = [
  { id: "all", label: "All" },
  { id: "two_person", label: "Needs two people" },
  { id: "exclusions", label: "Not a repayment" },
];

// The reasons a reviewer gives most. The reason goes with the line to whoever looks next, so a
// short list keeps them comparable; "Something else" takes free text.
const REASONS = ["Wrong borrower", "Wrong amount or split", "Not a repayment", "Needs more evidence"];
const OTHER = "Something else";

/** Proposals waiting for a person, largest payment first. Approve or reject, within your limit. */
export function Review() {
  const { staff } = useSession();
  const [params, setParams] = useSearchParams();
  const view = (params.get("view") as QueueView) || "all";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const { data, error, loading, reload } = useLoad(() => api.queue({ view, limit: PAGE, offset }), [view, offset]);

  function go(next: { view?: QueueView; offset?: number }) {
    const p = new URLSearchParams();
    const v = next.view ?? view;
    if (v !== "all") p.set("view", v);
    if (next.offset) p.set("offset", String(next.offset));
    setParams(p);
  }

  if (!staff) return null;
  const readOnly = staff.role === "auditor";

  return (
    <>
      <div className="head">
        <div>
          <h1>Review queue</h1>
          <p className="sub">
            Largest first.{" "}
            {readOnly
              ? "You can see every proposal, and decide none."
              : `You can approve up to ${naira(staff.limit_kobo)}.`}{" "}
            Above ₦500,000 two people approve, and one of them must have a limit that covers the amount.
          </p>
        </div>
      </div>

      <div className="filters" role="group" aria-label="Which proposals">
        {VIEWS.map((v) => (
          <button key={v.id} className="chip" aria-pressed={view === v.id} onClick={() => go({ view: v.id, offset: 0 })}>
            {v.label}
            {data && ` ${data.counts[v.id].toLocaleString()}`}
          </button>
        ))}
      </div>

      {error ? (
        <Failed error={error} retry={reload} />
      ) : !data ? (
        <Loading what="the queue" />
      ) : data.items.length === 0 ? (
        <div className="panel empty">
          Nothing waiting{view !== "all" && " in this view"}. New proposals arrive as rules and, from milestone 3, the agent
          work through incoming payments.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }} aria-busy={loading}>
          {data.items.map((item) => (
            <Proposal key={item.proposal_id} item={item} readOnly={readOnly} onDecided={reload} />
          ))}
          <div className="panel">
            <Pager offset={offset} limit={PAGE} total={data.total} onPage={(o) => go({ offset: o })} />
            {data.total <= PAGE && (
              <div className="pager">
                <span>
                  {data.total} waiting{view !== "all" && " in this view"}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function Proposal({ item, readOnly, onDecided }: { item: QueueItem; readOnly: boolean; onDecided: () => void }) {
  const toast = useToast();
  const [rejecting, setRejecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const { line, you } = item;
  const two = item.approvals_needed === 2;
  const first = item.approvals[0];
  // Whoever gave the first approval can't also reject: the API refuses it, so the button says so first.
  const approvedByYou = you.refused === "same_person";

  async function decide(action: () => Promise<string>) {
    setBusy(true);
    try {
      toast(await action());
    } catch (e) {
      toast(
        e instanceof ApiError && e.status === 409
          ? "Someone decided this one while you were looking. The queue has been refreshed."
          : e instanceof Error
            ? e.message
            : "That didn't work.",
      );
    } finally {
      setBusy(false);
      setRejecting(false);
      onDecided();
      announceChange();
    }
  }

  const approve = () =>
    decide(async () => {
      const res = await api.approve(item.proposal_id);
      if (res.status === "first_approval") return `First approval recorded for ${naira(line.amount_kobo)}. Someone else gives the second.`;
      return item.kind === "exclude"
        ? `Approved: ${naira(line.amount_kobo)} is excluded as not a repayment.`
        : `Approved ${naira(line.amount_kobo)}. It's queued for posting to Fineract and Odoo.`;
    });

  const reject = (reason: string) =>
    decide(async () => {
      await api.reject(item.proposal_id, reason);
      return `Rejected: ${reason.toLowerCase()}. The line goes to the investigator with your reason.`;
    });

  return (
    <article className="queue-item" aria-label={`${naira(line.amount_kobo)} from ${line.payer_name ?? "an unnamed payer"}`}>
      <div>
        <span className="ch">
          {CHANNEL_LABEL[line.channel] ?? line.channel} · {day(line.value_date)}
        </span>
        <span className="big">{naira(line.amount_kobo)}</span>
        <span className="narr">{line.narration}</span>
        {line.payer_name && <span className="note">Payer: {line.payer_name}</span>}
        {line.customer_reference && <span className="note mono">Reference typed: {line.customer_reference}</span>}
        <Link to={lineHref(line.id)} className="note">
          Open the full line
        </Link>
      </div>

      <div>
        <span className="ch">
          {actorLabel(item.proposed_by)}
          {item.confidence < 1 ? ` · confidence ${Math.round(item.confidence * 100)}%` : " · certain"}
        </span>
        {item.kind === "exclude" ? (
          <b>Exclude: not a repayment</b>
        ) : (
          item.allocations.map((a) => (
            <div key={a.loan_ref} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span className="mono">{a.loan_ref}</span>
              <span className="num">{naira(a.amount_kobo)}</span>
            </div>
          ))
        )}
        <Checks evidence={item.evidence} />
      </div>

      <footer>
        <span>
          {first ? (
            <span className="pill s-approved">1 of 2 approvals: {first.name}</span>
          ) : two ? (
            <span className="pill s-two">Needs two people</span>
          ) : null}
          {!you.can_approve && you.message && !readOnly && (
            <span className="note" style={{ marginLeft: 8 }}>
              {you.message}
            </span>
          )}
        </span>
        {readOnly ? (
          <span className="note">Read only</span>
        ) : rejecting ? (
          <RejectForm id={item.proposal_id} busy={busy} onReject={reject} onCancel={() => setRejecting(false)} />
        ) : (
          <div className="actions">
            <button
              className="btn"
              disabled={busy || approvedByYou}
              title={approvedByYou ? "You approved this one. Ask someone else to reject it." : undefined}
              onClick={() => setRejecting(true)}
            >
              Reject
            </button>
            <button
              className="btn primary"
              disabled={busy || !you.can_approve}
              title={you.can_approve ? undefined : (you.message ?? undefined)}
              onClick={approve}
            >
              {busy ? "Saving…" : first && you.can_approve ? "Give second approval" : you.can_approve && !you.settles ? "Approve (first of two)" : "Approve"}
            </button>
          </div>
        )}
      </footer>
    </article>
  );
}

function RejectForm({
  id,
  busy,
  onReject,
  onCancel,
}: {
  id: number;
  busy: boolean;
  onReject: (reason: string) => void;
  onCancel: () => void;
}) {
  const [choice, setChoice] = useState(REASONS[0]);
  const [other, setOther] = useState("");
  const reason = choice === OTHER ? other.trim() : choice;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (reason) onReject(reason);
  }

  return (
    <form className="reject-form" onSubmit={submit}>
      <label htmlFor={`why-${id}`}>Reason</label>
      <select id={`why-${id}`} value={choice} onChange={(e) => setChoice(e.target.value)} autoFocus>
        {[...REASONS, OTHER].map((r) => (
          <option key={r}>{r}</option>
        ))}
      </select>
      {choice === OTHER && (
        <input
          aria-label="Your reason"
          value={other}
          maxLength={500}
          onChange={(e) => setOther(e.target.value)}
          placeholder="Say why"
          required
          style={{ background: "var(--sunk)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "5px 8px" }}
        />
      )}
      <button className="btn danger" type="submit" disabled={busy || !reason}>
        Reject
      </button>
      <button className="btn" type="button" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}
