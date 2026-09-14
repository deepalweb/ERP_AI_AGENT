import { useRef, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchBusinessHealth, fetchDashboardSummary, fetchNotificationsSummary, fetchStoreScope } from "./api";
import Header from "./Header";
import Home from "./routes/Home";
import Dashboard from "./routes/Dashboard";
import CustomerPredictions from "./routes/CustomerPredictions";
import AskCelesteOverlay from "./chat/AskCelesteOverlay";

export default function App() {
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

  const { data: storeScope } = useQuery({ queryKey: ["storeScope"], queryFn: fetchStoreScope, select: (r) => r.stores });
  const { data: dashboardData } = useQuery({ queryKey: ["dashboardSummary"], queryFn: fetchDashboardSummary });
  const { data: health } = useQuery({ queryKey: ["businessHealth"], queryFn: fetchBusinessHealth });
  const { data: notifications } = useQuery({ queryKey: ["notificationsSummary"], queryFn: fetchNotificationsSummary });

  // /intelligence/health's payload has its own `status` field (the health
  // verdict) that overwrites the route wrapper's `"status": "ok"` — `overall`
  // being a number is the real success signal, not `health.status === "ok"`.
  const headerKpis =
    dashboardData?.status === "ok" && typeof health?.overall === "number"
      ? {
          revenue: dashboardData.sales.headline_revenue,
          orders: dashboardData.sales.headline_orders,
          healthScore: health.overall,
          healthStatus: health.status,
          dataThroughMonth: dashboardData.sales.months?.[dashboardData.sales.months.length - 1]?.month ?? null,
        }
      : null;

  return (
    <div className="min-h-screen bg-background">
      <Header
        kpis={headerKpis}
        storeScope={storeScope}
        onAskCeleste={() => setOverlayOpen(true)}
        newCriticalCount={notifications?.status === "ok" ? notifications.new_critical_count : 0}
      />

      <Routes>
        <Route path="/" element={<Home onAskCeleste={askCeleste} />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/customers/predictions" element={<CustomerPredictions />} />
      </Routes>

      <AskCelesteOverlay open={overlayOpen} question={pendingQuestion} onClose={() => setOverlayOpen(false)} />
    </div>
  );
}
