export default function ChatSidebar({ conversations, activeId, collapsed, onNewChat, onSelect, onDelete, onToggleCollapse }) {
  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);

  return (
    <aside className={`chat-sidebar${collapsed ? " chat-sidebar-collapsed" : ""}`}>
      <div className="chat-sidebar-top">
        <button type="button" className="chat-sidebar-new-btn" onClick={onNewChat} title="New chat">
          <span className="chat-sidebar-new-icon" aria-hidden="true">
            &#9998;
          </span>
          {!collapsed && <span>New chat</span>}
        </button>
        <button
          type="button"
          className="chat-sidebar-collapse-btn"
          onClick={onToggleCollapse}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
        >
          {collapsed ? "›" : "‹"}
        </button>
      </div>

      {!collapsed && (
        <>
          <div className="chat-sidebar-section-label">Recents</div>
          <ul className="chat-sidebar-list">
            {sorted.map((c) => (
              <li
                key={c.id}
                className={`chat-sidebar-item${c.id === activeId ? " chat-sidebar-item-active" : ""}`}
              >
                <button type="button" className="chat-sidebar-item-btn" onClick={() => onSelect(c.id)}>
                  {c.title}
                </button>
                <button
                  type="button"
                  className="chat-sidebar-item-delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(c.id);
                  }}
                  title="Delete conversation"
                  aria-label={`Delete conversation "${c.title}"`}
                >
                  &times;
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}
