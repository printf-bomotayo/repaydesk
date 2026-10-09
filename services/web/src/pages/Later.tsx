import { useLocation } from "react-router";
import { AREAS } from "../components/Shell";

const WHAT: Record<string, string> = {
  "/today": "The week at a glance: lines by status and channel, suspense by age, the latest agent runs.",
  "/runs": "Each investigation by the agent: the tools it called, what they returned, and what it proposed.",
  "/collections": "Arrears by bucket, drafted messages for approval, promises to pay.",
  "/measures": "Time per 100 lines against the baseline, auto-match rate, suspense age, cost per line.",
};

/** A screen that is in the plan and the mockup, but not built yet. Says when it arrives. */
export function Later() {
  const { pathname } = useLocation();
  const area = AREAS.flatMap((g) => g.areas).find((a) => a.to === pathname);
  return (
    <>
      <div className="head">
        <div>
          <h1>{area?.label ?? "Not built yet"}</h1>
          <p className="sub">{WHAT[pathname]}</p>
        </div>
        {area?.later && <span className="pill s-new">Arrives in {area.later}</span>}
      </div>
      <div className="panel empty">This screen is planned, not built yet.</div>
    </>
  );
}
