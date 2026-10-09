import { useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { useSession } from "../session";

// The demo bank's reviewers, offered as a list so a visitor can try each role. A bank's own
// deployment signs in through its single sign-on instead (plan, stage 4).
const DEMO = [
  { id: "amaka", label: "Amaka Nwosu · officer, up to ₦500,000" },
  { id: "kunle", label: "Kunle Adebayo · officer, up to ₦500,000" },
  { id: "tunde", label: "Tunde Bakare · supervisor, up to ₦5,000,000" },
  { id: "hadiza", label: "Hadiza Lawal · auditor, read only" },
];

export function SignIn() {
  const { signIn, config, ended } = useSession();
  const [staffId, setStaffId] = useState(DEMO[0].id);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(staffId, password);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Wrong name or password." : "Couldn't reach Repaydesk. Is the API running?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <form className="panel" onSubmit={onSubmit}>
        <div>
          <h1>Repaydesk</h1>
          <p className="sub">{config?.bank_name ?? " "}</p>
        </div>
        <span className="proto" style={{ alignSelf: "flex-start" }}>
          Prototype · synthetic data
        </span>
        {ended && !error && <div className="error">{ended}</div>}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <label>
          Who you are
          <select value={staffId} onChange={(e) => setStaffId(e.target.value)}>
            {DEMO.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="note">The demo reviewers share one password, set as STAFF_DEMO_PASSWORD in .env.</p>
      </form>
    </div>
  );
}
