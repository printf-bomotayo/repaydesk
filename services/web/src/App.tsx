import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { Shell } from "./components/Shell";
import { Loading, ToastProvider } from "./components/common";
import { Review } from "./pages/Review";
import { Audit } from "./pages/Audit";
import { Collections } from "./pages/Collections";
import { BorrowerDetail, Borrowers } from "./pages/Borrowers";
import { Ingestion } from "./pages/Ingestion";
import { Later } from "./pages/Later";
import { LineDetail } from "./pages/LineDetail";
import { LoanDetail, Loans } from "./pages/Loans";
import { Payments } from "./pages/Payments";
import { RunPage, Runs } from "./pages/Runs";
import { SignIn } from "./pages/SignIn";
import { Today } from "./pages/Today";
import { SessionProvider, useSession } from "./session";

const LATER = ["/measures"];

function Routed() {
  const { staff, checking } = useSession();
  if (checking) return <Loading what="your session" />;
  if (!staff) return <SignIn />;
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<Today />} />
        <Route path="/payments" element={<Payments />} />
        <Route path="/payments/:id" element={<LineDetail />} />
        <Route path="/review" element={<Review />} />
        <Route path="/borrowers" element={<Borrowers />} />
        <Route path="/borrowers/:ref" element={<BorrowerDetail />} />
        <Route path="/loans" element={<Loans />} />
        <Route path="/loans/:ref" element={<LoanDetail />} />
        <Route path="/ingestion" element={<Ingestion />} />
        <Route path="/collections" element={<Collections />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/audit" element={<Audit />} />
        {LATER.map((p) => (
          <Route key={p} path={p} element={<Later />} />
        ))}
        <Route path="*" element={<Navigate to="/today" replace />} />
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
