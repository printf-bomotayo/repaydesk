import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken, setToken, whenUnauthorised, type Config, type Staff } from "./api";

interface Session {
  staff: Staff | null;
  config: Config | null;
  /** True until we know whether a stored token is still good. */
  checking: boolean;
  signIn: (staffId: string, password: string) => Promise<void>;
  signOut: () => void;
  /** Set when the API ended the session, so the sign-in screen can say why. */
  ended: string | null;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [staff, setStaff] = useState<Staff | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [checking, setChecking] = useState(() => getToken() !== null);
  const [ended, setEnded] = useState<string | null>(null);

  useEffect(() => {
    api.config().then(setConfig, () => setConfig(null));
    whenUnauthorised(() => {
      setStaff(null);
      setEnded("Your session ended. Sign in again.");
    });
    if (getToken()) {
      api
        .me()
        .then((me) => setStaff(me), () => setStaff(null))
        .finally(() => setChecking(false));
    }
  }, []);

  const signIn = useCallback(async (staffId: string, password: string) => {
    const res = await api.signIn(staffId, password);
    setToken(res.token);
    setEnded(null);
    setStaff(res.staff);
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setStaff(null);
  }, []);

  const value = useMemo(
    () => ({ staff, config, checking, signIn, signOut, ended }),
    [staff, config, checking, signIn, signOut, ended],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}
