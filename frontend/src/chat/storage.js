const STORAGE_KEY = "celeste-chat-conversations";
const TITLE_MAX_LENGTH = 40;
// Every "Ask Celeste about this" click (KPI tiles, tracked issues, a
// customer row) starts a brand-new saved conversation -- with that many
// entry points now wired up, history grows faster than a single chat
// window ever did. Oldest-by-updatedAt conversations are dropped past
// this cap, not a message-count limit per conversation: an open question
// a manager is actually working through should never get truncated
// mid-thread, only old abandoned ones should age out.
const MAX_CONVERSATIONS = 50;

function emptyConversation() {
  return {
    id: crypto.randomUUID(),
    title: "New chat",
    messages: [],
    updatedAt: Date.now(),
  };
}

export function titleFromMessage(text) {
  const trimmed = text.trim();
  if (trimmed.length <= TITLE_MAX_LENGTH) return trimmed;
  return `${trimmed.slice(0, TITLE_MAX_LENGTH).trimEnd()}…`;
}

export function loadConversations() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed.conversations) && parsed.conversations.length > 0) {
        const activeId = parsed.conversations.some((c) => c.id === parsed.activeId)
          ? parsed.activeId
          : parsed.conversations[0].id;
        return { conversations: parsed.conversations, activeId };
      }
    }
  } catch {
    // Corrupt or inaccessible storage — fall through to a fresh conversation.
  }
  const fresh = emptyConversation();
  return { conversations: [fresh], activeId: fresh.id };
}

// Trims what's PERSISTED, not the caller's in-memory React state -- the
// in-memory list can briefly exceed MAX_CONVERSATIONS (harmless, it's
// just the sidebar list) and reconciles to the capped set on next
// loadConversations(), same as a page refresh already does.
export function saveConversations(state) {
  try {
    let { conversations } = state;
    if (conversations.length > MAX_CONVERSATIONS) {
      conversations = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONVERSATIONS);
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...state, conversations }));
  } catch {
    // Private-mode/full storage — chat still works, it just won't persist.
  }
}

export function newConversation() {
  return emptyConversation();
}
