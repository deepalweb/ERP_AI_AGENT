const STORAGE_KEY = "celeste-chat-conversations";
const TITLE_MAX_LENGTH = 40;

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

export function saveConversations(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Private-mode/full storage — chat still works, it just won't persist.
  }
}

export function newConversation() {
  return emptyConversation();
}
