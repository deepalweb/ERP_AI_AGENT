import { useRef, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchStoreScope } from "./api";
import Header from "./Header";
import Home from "./routes/Home";
import Actions from "./routes/Actions";
import Dashboard from "./routes/Dashboard";
import CustomerPredictions from "./routes/CustomerPredictions";
import Investigate from "./routes/Investigate";
import Inventory from "./routes/Inventory";
import CaseDetail from "./routes/CaseDetail";
import Outcomes from "./routes/Outcomes";
import AskCelesteOverlay from "./chat/AskCelesteOverlay";

export default function App({ onSignOut }) {
  const [overlayOpen, setOverlayOpen] = useState(false);
  // {text, key} — key increments on every "Ask Celeste about this" click so
  // asking the identical question twice in a row still triggers a fresh
  // send (AskCelesteOverlay watches key changing, not just text).
  const [pendingQuestion, setPendingQuestion] = useState(null);
  const questionKeyRef = useRef(0);

  function askCeleste(text) {
    questionKeyRef.current += 1;
    setPendingQuestion({ text, key: questionKeyRef.current });
    setOverlayOpen(true);
  }

  const { data: storeScope } = useQuery({
    queryKey: ["storeScope"],
    queryFn: fetchStoreScope,
    select: (r) => r.stores,
  });

  return (
    <div className="min-h-screen bg-background">
      <Header
        storeScope={storeScope}
        onAskCeleste={() => setOverlayOpen(true)}
        onAskCelesteQuestion={askCeleste}
        onSignOut={onSignOut}
      />

      <main id="main-content" tabIndex={-1} className="workspace-main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/actions" element={<Actions />} />
          <Route path="/investigate" element={<Investigate />} />
          <Route path="/investigate/:caseId" element={<CaseDetail />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/outcomes" element={<Outcomes />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route
            path="/customers/predictions"
            element={<CustomerPredictions />}
          />
        </Routes>
      </main>

      <AskCelesteOverlay
        open={overlayOpen}
        question={pendingQuestion}
        onClose={() => setOverlayOpen(false)}
      />
    </div>
  );
}
