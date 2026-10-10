import { useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import { announceChange, useToast } from "./common";

/** Sends a failed posting back to the gateway, once a person has dealt with what stopped it. The
 * gateway rechecks the approval, and the idempotency key keeps anything already posted from
 * posting twice. A posting the gateway refused would only be refused again, so it has no button. */
export function RetryPosting({ outboxId, refused, onDone }: { outboxId: number; refused: boolean; onDone?: () => void }) {
  const { staff } = useSession();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (refused) return <span className="note">Needs a new proposal and decision</span>;
  if (!staff || staff.role === "auditor") return <span className="note">Read only</span>;

  async function retry() {
    setBusy(true);
    try {
      await api.retryPosting(outboxId);
      toast("Sent back to the posting gateway. It rechecks the approval and posts within seconds.");
      announceChange();
      onDone?.();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button className="btn primary" disabled={busy} onClick={retry}>
      {busy ? "Sending back…" : "Retry posting"}
    </button>
  );
}
