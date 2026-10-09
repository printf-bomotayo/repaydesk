import { evidenceChecks } from "../evidence";

/** A proposal's evidence as a list of checks: ✓ agrees, ✕ disagrees, – couldn't tell. */
export function Checks({ evidence }: { evidence: Record<string, unknown> | null | undefined }) {
  return (
    <div>
      {evidenceChecks(evidence).map((c, i) => (
        <div key={i} className={`check ${c.ok === true ? "yes" : c.ok === false ? "no" : "na"}`}>
          <span className="m" aria-hidden="true">
            {c.ok === true ? "✓" : c.ok === false ? "✕" : "–"}
          </span>
          <span>{c.text}</span>
        </div>
      ))}
    </div>
  );
}
