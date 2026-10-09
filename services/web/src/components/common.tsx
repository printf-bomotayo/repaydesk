import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api";
import { STATUS_LABEL } from "../format";

/** Loads whenever `deps` change, ignoring answers to questions no longer being asked. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const run = useCallback(() => {
    const mine = ++seq.current;
    setLoading(true);
    load().then(
      (d) => {
        if (mine !== seq.current) return;
        setData(d);
        setError(null);
        setLoading(false);
      },
      (e: Error) => {
        if (mine !== seq.current) return;
        setError(e);
        setLoading(false);
      },
    );
  }, deps); // the caller lists what the load depends on

  useEffect(run, [run]);
  return { data, error, loading, reload: run };
}

export function Pill({ status, label }: { status: string; label?: string }) {
  return <span className={`pill s-${status}`}>{label ?? STATUS_LABEL[status] ?? status}</span>;
}

export function Loading({ what }: { what: string }) {
  return (
    <div className="loading" role="status">
      Loading {what}…
    </div>
  );
}

export function Failed({ error, retry }: { error: Error; retry?: () => void }) {
  return (
    <div className="error" role="alert">
      {error instanceof ApiError && error.status === 404 ? "Not found." : `Couldn't load this: ${error.message}`}{" "}
      {retry && (
        <button className="linkbtn" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Pager({
  offset,
  limit,
  total,
  onPage,
}: {
  offset: number;
  limit: number;
  total: number;
  onPage: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <nav className="pager" aria-label="Pages">
      <span>
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
      </span>
      <span className="actions">
        <button className="btn" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>
          Previous
        </button>
        <button className="btn" disabled={to >= total} onClick={() => onPage(offset + limit)}>
          Next
        </button>
      </span>
    </nav>
  );
}

export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children} ↗
    </a>
  );
}
