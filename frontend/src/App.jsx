import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";
import { fetchBusinessHealth, fetchDashboardSummary, fetchInvestigations, fetchStoreScope, sendChatMessage } from "./api";
import Header from "./Header";
import Overview from "./Overview";
import Operations from "./Operations";
import Investigation from "./Investigation";
import CustomerIntelligence from "./CustomerIntelligence";
import InventoryDepth from "./InventoryDepth";
import ForwardLook from "./ForwardLook";
import OperationsSummaryCard from "./chat/OperationsSummaryCard";
import InvestigateMetricCard from "./chat/InvestigateMetricCard";

function SalesSummaryCard({ result }) {
  if (!result || result.error) {
    return null;
  }
  return (
    <div className="tool-card">
      <div className="tool-card-title">Sales Summary &mdash; {result.period}</div>
      {result.is_partial_period && (
        <div className="tool-card-notice">
          Partial month &mdash; {result.days_elapsed} of {result.days_in_period} days elapsed.
          Growth vs. prior month isn&rsquo;t a like-for-like comparison.
        </div>
      )}
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Revenue</span>
          <span className="tool-card-value">
            ${result.revenue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </span>
        </div>
        <div>
          <span className="tool-card-label">Orders</span>
          <span className="tool-card-value">{result.orders.toLocaleString()}</span>
        </div>
        <div>
          <span className="tool-card-label">Avg. Order Value</span>
          <span className="tool-card-value">
            ${result.average_order_value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
        </div>
        <div>
          <span className="tool-card-label">Growth vs. Prev. Month</span>
          <span className="tool-card-value">
            {result.growth_vs_previous_month_pct === null
              ? "n/a"
              : `${result.growth_vs_previous_month_pct}%`}
          </span>
        </div>
      </div>
    </div>
  );
}

function ToolCalls({ toolCalls }) {
  if (!toolCalls || toolCalls.length === 0) {
    return null;
  }
  return (
    <div className="tool-calls">
      {toolCalls.map((call, i) => {
        switch (call.name) {
          case "get_sales_summary":
            return <SalesSummaryCard key={i} result={call.result} />;
          case "get_customer_intelligence":
            return <CustomerIntelligence key={i} data={call.result} />;
          case "get_inventory_depth":
            return <InventoryDepth key={i} data={call.result} />;
          case "get_forward_look":
            return <ForwardLook key={i} data={call.result} />;
          case "get_operations_summary":
            return <OperationsSummaryCard key={i} result={call.result} />;
          case "investigate_metric":
            return <InvestigateMetricCard key={i} result={call.result} />;
          default:
            return null;
        }
      })}
    </div>
  );
}

function Message({ message }) {
  const hasTools = message.toolCalls && message.toolCalls.length > 0;
  return (
    <div className={`message message-${message.role}${hasTools ? " message-has-tools" : ""}`}>
      <div className="message-bubble">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
      </div>
      <ToolCalls toolCalls={message.toolCalls} />
    </div>
  );
}

export default function App() {
  const [view, setView] = useState("overview");
  const [previousView, setPreviousView] = useState("overview");
  const [investigations, setInvestigations] = useState(null);
  const [tracking, setTracking] = useState(null);
  const [selectedInvestigationId, setSelectedInvestigationId] = useState(null);
  const [dashboardData, setDashboardData] = useState(null);
  const [health, setHealth] = useState(null);
  const [storeScope, setStoreScope] = useState(null);
  const [messages, setMessages] = useState([]);

  useEffect(() => {
    document.body.classList.toggle(
      "view-dashboard",
      view === "overview" || view === "operations" || view === "investigation",
    );
  }, [view]);

  useEffect(() => {
    let cancelled = false;
    fetchInvestigations().then((res) => {
      if (cancelled || res.status === "error") return;
      setInvestigations(res.investigations);
      setTracking(res.tracking);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchDashboardSummary(), fetchBusinessHealth()]).then(([dashRes, healthRes]) => {
      if (cancelled) return;
      if (dashRes.status !== "error") setDashboardData(dashRes);
      if (healthRes.status !== "error") setHealth(healthRes);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchStoreScope().then((res) => {
      if (cancelled || res.status === "error") return;
      setStoreScope(res.stores);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  function openInvestigation(id) {
    setSelectedInvestigationId(id);
    setPreviousView(view === "investigation" ? previousView : view);
    setView("investigation");
  }

  const headerKpis =
    dashboardData && health
      ? {
          revenue: dashboardData.sales.headline_revenue,
          orders: dashboardData.sales.headline_orders,
          healthScore: health.overall,
          healthStatus: health.status,
          dataThroughMonth: dashboardData.sales.months?.[dashboardData.sales.months.length - 1]?.month ?? null,
        }
      : null;

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    const nextMessages = [...messages, { role: "user", content: text }];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const data = await sendChatMessage(
        nextMessages.map(({ role, content }) => ({ role, content }))
      );

      if (data.status === "error") {
        setError(data.detail || "The agent failed to process this request.");
        return;
      }

      setMessages([
        ...nextMessages,
        { role: "assistant", content: data.reply, toolCalls: data.tool_calls },
      ]);
    } catch {
      setError("Could not reach the agent backend. Is it running on port 8000?");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <Header view={view} onNavigate={setView} kpis={headerKpis} storeScope={storeScope} />

      {view === "overview" && (
        <Overview
          investigations={investigations}
          tracking={tracking}
          onOpenInvestigation={openInvestigation}
          dashboardData={dashboardData}
          health={health}
        />
      )}
      {view === "operations" && (
        <Operations investigations={investigations} onOpenInvestigation={openInvestigation} />
      )}
      {view === "investigation" && (
        <Investigation
          investigation={investigations?.find((i) => i.id === selectedInvestigationId)}
          tracking={tracking}
          onBack={() => setView(previousView)}
        />
      )}
      {view === "chat" && (
        <>
          <main className="chat-window">
            {messages.length === 0 && (
              <p className="empty-state">
                Ask something like &ldquo;How were sales last month?&rdquo;
              </p>
            )}
            {messages.map((m, i) => (
              <Message key={i} message={m} />
            ))}
            {loading && <div className="message message-assistant">
              <div className="message-bubble message-loading">Thinking&hellip;</div>
            </div>}
            {error && <div className="error-banner">{error}</div>}
          </main>

          <form className="chat-input" onSubmit={handleSend}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your ERP data&hellip;"
              disabled={loading}
            />
            <button type="submit" disabled={loading || !input.trim()}>
              Send
            </button>
          </form>
        </>
      )}
    </div>
  );
}
