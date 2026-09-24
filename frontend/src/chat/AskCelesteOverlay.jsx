import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./legacy-cards.css";
import { sendChatMessage } from "../api";
import ChatSidebar from "./ChatSidebar";
import { loadConversations, saveConversations, newConversation, titleFromMessage } from "./storage";
import CustomerIntelligence from "../CustomerIntelligence";
import InventoryDepth from "../InventoryDepth";
import ForwardLook from "../ForwardLook";
import OperationsSummaryCard from "./OperationsSummaryCard";
import InvestigateMetricCard from "./InvestigateMetricCard";
import TopProductsCard from "./TopProductsCard";
import CancelledProductsCard from "./CancelledProductsCard";
import MarginCard from "./MarginCard";
import ChannelBreakdownCard from "./ChannelBreakdownCard";
import UberLostSalesCard from "./UberLostSalesCard";
import InventoryByOutletCard from "./InventoryByOutletCard";
import ProductLookupCard from "./ProductLookupCard";
import StockAlertsCard from "./StockAlertsCard";
import ProductListingCard from "./ProductListingCard";
import AttentionScanCard from "./AttentionScanCard";
import ConcentrationCard from "./ConcentrationCard";
import BasketAnalysisCard from "./BasketAnalysisCard";
import BrandMixCard from "./BrandMixCard";
import LossAndDamageCard from "./LossAndDamageCard";
import LoyaltyPromotionsCard from "./LoyaltyPromotionsCard";
import StockCountVarianceCard from "./StockCountVarianceCard";
import DataControlsCard from "./DataControlsCard";

/** Ported from git history's pre-rebuild App.jsx (not a new design) — was
 * never a separate file since it's small. Kept on the same .tool-card
 * styling as everything else here rather than invented fresh. */
function SalesSummaryCard({ result }) {
  if (!result || result.error) return null;
  return (
    <div className="tool-card">
      <div className="tool-card-title">Sales Summary &mdash; {result.period}</div>
      {result.is_partial_period && (
        <div className="tool-card-notice">
          Partial month &mdash; {result.days_elapsed} of {result.days_in_period} days elapsed. Growth vs. prior
          month isn&rsquo;t a like-for-like comparison.
        </div>
      )}
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Revenue</span>
          <span className="tool-card-value">${result.revenue.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
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
          <span className="tool-card-label">Growth vs. Prev. Period</span>
          <span className="tool-card-value">
            {result.growth_vs_previous_month_pct === null ? "n/a" : `${result.growth_vs_previous_month_pct}%`}
          </span>
        </div>
      </div>
    </div>
  );
}

/** Tool name -> specialized result card. Ported from the pre-rebuild
 * App.jsx's switch, plus 4 cases that were missing there even before this
 * rebuild (get_basket_analysis, get_brand_and_department_mix,
 * get_loss_and_damage, get_loyalty_and_promotions — added in a later
 * session, wired into Insights.jsx, but never into this switch). list_metrics
 * gets no card — it's a metadata listing, nothing meaningful to render. */
function ToolCalls({ toolCalls }) {
  if (!toolCalls || toolCalls.length === 0) return null;
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
          case "get_top_products":
            return <TopProductsCard key={i} result={call.result} />;
          case "get_top_cancelled_products":
            return <CancelledProductsCard key={i} result={call.result} />;
          case "get_gross_margin":
            return <MarginCard key={i} result={call.result} />;
          case "get_channel_breakdown":
            return <ChannelBreakdownCard key={i} result={call.result} />;
          case "get_uber_lost_sales":
            return <UberLostSalesCard key={i} result={call.result} />;
          case "get_inventory_by_outlet":
            return <InventoryByOutletCard key={i} result={call.result} />;
          case "get_product_lookup":
            return <ProductLookupCard key={i} result={call.result} />;
          case "get_stock_alerts":
            return <StockAlertsCard key={i} result={call.result} />;
          case "get_product_listing":
            return <ProductListingCard key={i} result={call.result} />;
          case "scan_attention":
            return <AttentionScanCard key={i} result={call.result} />;
          case "get_revenue_concentration":
            return <ConcentrationCard key={i} result={call.result} />;
          case "get_basket_analysis":
            return <BasketAnalysisCard key={i} result={call.result} />;
          case "get_brand_and_department_mix":
            return <BrandMixCard key={i} result={call.result} />;
          case "get_loss_and_damage":
            return <LossAndDamageCard key={i} result={call.result} />;
          case "get_loyalty_and_promotions":
            return <LoyaltyPromotionsCard key={i} result={call.result} />;
          case "get_stock_count_variance":
            return <StockCountVarianceCard key={i} result={call.result} />;
          case "get_data_controls":
            return <DataControlsCard key={i} result={call.result} />;
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

/** The real "Ask Celeste" experience — was a placeholder overlay. Ports the
 * pre-rebuild App.jsx's full chat page (message list, tool-result cards,
 * conversation history/sidebar) into a slide-over instead of a full route,
 * reusing every existing chat/* component as-is. `question` is a request
 * object {text, key} from a Situation Card's "Ask Celeste about this" — a
 * new `key` (not just new text, since two clicks can carry identical text)
 * starts a fresh conversation and sends it immediately. */
export default function AskCelesteOverlay({ open, question, onClose }) {
  const dialogRef = useRef(null);
  const [{ conversations, activeId }, setChatState] = useState(loadConversations);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.innerWidth < 640);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [handledKey, setHandledKey] = useState(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    const trigger = document.activeElement;
    if (open && dialog && !dialog.open) dialog.showModal();
    return () => {
      if (dialog?.open) dialog.close();
      if (open && trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, [open]);

  useEffect(() => {
    saveConversations({ conversations, activeId });
  }, [conversations, activeId]);

  const activeConversation = conversations.find((c) => c.id === activeId) ?? conversations[0];
  const activeMessages = activeConversation?.messages ?? [];

  function handleNewChat() {
    const fresh = newConversation();
    setChatState((prev) => ({ conversations: [fresh, ...prev.conversations], activeId: fresh.id }));
    setError(null);
    return fresh.id;
  }

  function handleSelectConversation(id) {
    setChatState((prev) => ({ ...prev, activeId: id }));
    setError(null);
  }

  function handleDeleteConversation(id) {
    setChatState((prev) => {
      const remaining = prev.conversations.filter((c) => c.id !== id);
      if (remaining.length === 0) {
        const fresh = newConversation();
        return { conversations: [fresh], activeId: fresh.id };
      }
      const activeId = prev.activeId === id ? [...remaining].sort((a, b) => b.updatedAt - a.updatedAt)[0].id : prev.activeId;
      return { conversations: remaining, activeId };
    });
  }

  async function sendText(text, baseMessages, targetId) {
    if (!text || loading) return;
    const nextMessages = [...baseMessages, { role: "user", content: text }];
    setChatState((prev) => ({
      ...prev,
      conversations: prev.conversations.map((c) =>
        c.id === targetId
          ? { ...c, messages: nextMessages, title: c.title === "New chat" ? titleFromMessage(text) : c.title, updatedAt: Date.now() }
          : c,
      ),
    }));
    setLoading(true);
    setError(null);

    try {
      const data = await sendChatMessage(nextMessages.map(({ role, content }) => ({ role, content })));
      if (data.status === "error") {
        setError(data.detail || "The agent failed to process this request.");
        return;
      }
      setChatState((prev) => ({
        ...prev,
        conversations: prev.conversations.map((c) =>
          c.id === targetId
            ? { ...c, messages: [...nextMessages, { role: "assistant", content: data.reply, toolCalls: data.tool_calls }], updatedAt: Date.now() }
            : c,
        ),
      }));
    } catch {
      setError("Could not reach the agent backend. Is it running on port 8000?");
    } finally {
      setLoading(false);
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    await sendText(text, activeMessages, activeId);
  }

  // A Situation Card's "Ask Celeste about this" arrives as {text, key} — key
  // changes on every click even if the text is identical to a prior one, so
  // re-asking the same question twice still triggers a fresh send.
  useEffect(() => {
    if (!open || !question || question.key === handledKey) return;
    setHandledKey(question.key);
    const freshId = handleNewChat();
    sendText(question.text, [], freshId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, question, handledKey]);

  if (!open) return null;

  return (
    <dialog ref={dialogRef} aria-labelledby="ask-celeste-title" className="ask-dialog" onCancel={(e) => { e.preventDefault(); onClose(); }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="ask-dialog-panel flex h-full w-full max-w-4xl bg-background shadow-xl">
        <ChatSidebar
          conversations={conversations}
          activeId={activeId}
          collapsed={sidebarCollapsed}
          onNewChat={handleNewChat}
          onSelect={handleSelectConversation}
          onDelete={handleDeleteConversation}
          onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
        />

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <div><p className="eyebrow">Conversational intelligence</p><h2 id="ask-celeste-title" className="mt-1 font-display text-lg font-semibold text-foreground-strong">Ask Celeste</h2></div>
            <button type="button" onClick={onClose} className="text-foreground/60 hover:text-foreground">
              Close
            </button>
          </div>

          <main className="flex-1 space-y-4 overflow-y-auto p-6">
            {activeMessages.length === 0 && (
              <div className="chat-welcome"><p className="eyebrow">Start with a question</p><h3>A closer look at<br />your business.</h3><p>Ask about sales, products, inventory, or customer patterns.</p><p className="mt-3 text-xs text-foreground-subtle">Saved case evidence and decisions are available in Investigate.</p><button type="button" onClick={() => setInput("How were sales last month?")}>How were sales last month? →</button></div>
            )}
            {activeMessages.map((m, i) => (
              <Message key={i} message={m} />
            ))}
            {loading && (
              <div className="message message-assistant">
                <div className="message-bubble message-loading">Thinking&hellip;</div>
              </div>
            )}
            {error && <div className="text-sm text-critical">{error}</div>}
          </main>

          <form className="flex gap-2 border-t border-border p-4" onSubmit={handleSend}>
            <input
              type="text"
              aria-label="Message to Celeste"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your ERP data…"
              disabled={loading}
              className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </div>
      </div>
    </dialog>
  );
}
