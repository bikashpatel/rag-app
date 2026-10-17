import { memo, useCallback, useRef } from "react";
import type { ConversationSummary, Me } from "../../api/types";
import ConversationItem from "./ConversationItem";
import { CloseIcon, NewChatIcon, RetryIcon } from "./icons";
import type { ConversationsState } from "./useConversations";
import { useDrawer } from "./useDrawer";
import UsageMeter from "./UsageMeter";
import UserMenu from "./UserMenu";

const DAY_MS = 86_400_000;

type ConversationGroup = { label: string; items: ConversationSummary[] };

function groupByRecency(conversations: ConversationSummary[], now: Date): ConversationGroup[] {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const groups: ConversationGroup[] = [
    { label: "Today", items: [] },
    { label: "Previous 7 days", items: [] },
    { label: "Older", items: [] },
  ];
  for (const conversation of conversations) {
    const updated = Date.parse(conversation.updated_at);
    const group = updated >= startOfToday ? 0 : updated >= startOfToday - 7 * DAY_MS ? 1 : 2;
    groups[group].items.push(conversation);
  }
  return groups.filter((group) => group.items.length > 0);
}

type ConversationSidebarProps = {
  id: string;
  conversations: ConversationsState;
  activeId: number | null;
  user: Me | null;
  /** A chat model the user can pick generates images (the usage meter then shows images). */
  imagesOffered: boolean;
  /** Medium and narrow screens: the sidebar is an overlay drawer. */
  drawer: boolean;
  open: boolean;
  onClose: () => void;
  /** A conversation link was followed. */
  onNavigate: () => void;
  onNewChat: () => void;
  onDeleted: (id: number) => void;
  onChangePassword: () => void;
  /** Account menu → Connections (offered when any plugin is available). */
  onOpenConnections: () => void;
  onLogout: () => void;
};

function ConversationSidebar({
  id,
  conversations: { conversations, error, reload, rename, remove },
  activeId,
  user,
  imagesOffered,
  drawer,
  open,
  onClose,
  onNavigate,
  onNewChat,
  onDeleted,
  onChangePassword,
  onOpenConnections,
  onLogout,
}: ConversationSidebarProps) {
  const rootRef = useRef<HTMLElement>(null);
  const isOpenDrawer = drawer && open;
  useDrawer(isOpenDrawer, rootRef, onClose);

  const deleteConversation = useCallback(
    async (conversationId: number) => {
      await remove(conversationId);
      onDeleted(conversationId);
    },
    [remove, onDeleted],
  );

  const className = ["chat-sidebar", drawer ? "chat-sidebar-drawer" : "", isOpenDrawer ? "chat-sidebar-open" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <aside
      ref={rootRef}
      id={id}
      className={className}
      aria-label="Conversations"
      role={isOpenDrawer ? "dialog" : undefined}
      aria-modal={isOpenDrawer || undefined}
      tabIndex={-1}
    >
      <div className="chat-sidebar-header">
        <div className="chat-brand">
          <span className="chat-brand-logo" aria-hidden="true">
            R
          </span>
          <span className="chat-brand-name">RAG Assistant</span>
        </div>
        {drawer ? (
          <button type="button" className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close conversations">
            <CloseIcon size={16} />
          </button>
        ) : null}
      </div>

      <div className="chat-sidebar-new">
        <button type="button" className="btn chat-new-chat" onClick={onNewChat}>
          <NewChatIcon size={15} />
          New chat
        </button>
      </div>

      <nav className="chat-conv-nav" aria-label="Conversation history">
        {conversations === null && error ? (
          <div className="chat-sidebar-message" role="alert">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => void reload()}>
              <RetryIcon size={13} />
              Try again
            </button>
          </div>
        ) : conversations === null ? (
          <div className="chat-conv-skeletons" aria-busy="true">
            <span className="sr-only" role="status">
              Loading conversations
            </span>
            {[72, 56, 84, 64, 48].map((width) => (
              <span key={width} className="chat-skeleton chat-conv-skeleton" style={{ width: `${width}%` }} />
            ))}
          </div>
        ) : conversations.length === 0 ? (
          <p className="chat-sidebar-message">No conversations yet. Your chats will appear here.</p>
        ) : (
          groupByRecency(conversations, new Date()).map((group) => (
            <section key={group.label} className="chat-conv-group">
              <h2 className="chat-conv-group-label">{group.label}</h2>
              <ul className="chat-conv-list">
                {group.items.map((conversation) => (
                  <ConversationItem
                    key={conversation.id}
                    conversation={conversation}
                    active={conversation.id === activeId}
                    onOpen={onNavigate}
                    onRename={rename}
                    onDelete={deleteConversation}
                  />
                ))}
              </ul>
            </section>
          ))
        )}
      </nav>

      {user ? (
        <div className="chat-sidebar-footer">
          <UsageMeter user={user} imagesOffered={imagesOffered} />
          <UserMenu
            user={user}
            onChangePassword={onChangePassword}
            onOpenConnections={onOpenConnections}
            onLogout={onLogout}
          />
        </div>
      ) : null}
    </aside>
  );
}

export default memo(ConversationSidebar);
