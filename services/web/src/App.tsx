import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { Shell } from "./components/Shell";
import { Loading, ToastProvider } from "./components/common";
import { Review } from "./pages/Review";
import { Audit } from "./pages/Audit";
import { Ingestion } from "./pages/Ingestion";
import { Later } from "./pages/Later";
import { LineDetail } from "./pages/LineDetail";
import { Payments } from "./pages/Payments";
import { SignIn } from "./pages/SignIn";
import { SessionProvider, useSession } from "./session";

const LATER = ["/today", "/borrowers", "/loans", "/runs", "/collections", "/measures"];

function Routed() {
  const { staff, checking } = useSession();
  if (checking) return <Loading what="your session" />;
  if (!staff) return <SignIn />;
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/payments" replace />} />
        <Route path="/payments" element={<Payments />} />
        <Route path="/payments/:id" element={<LineDetail />} />
        <Route path="/review" element={<Review />} />
        <Route path="/ingestion" element={<Ingestion />} />
        <Route path="/audit" element={<Audit />} />
        {LATER.map((p) => (
          <Route key={p} path={p} element={<Later />} />
        ))}
        <Route path="*" element={<Navigate to="/payments" replace />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <SessionProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routed />
        </BrowserRouter>
      </ToastProvider>
    </SessionProvider>
  );
}
