import { memo, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import type { DocumentSelection } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import ChangePasswordModal from "../components/chat/ChangePasswordModal";
import "../components/chat/chat.css";
import ChatHeader from "../components/chat/ChatHeader";
import Composer, { type ComposerHandle } from "../components/chat/Composer";
import ConversationSidebar from "../components/chat/ConversationSidebar";
import EmptyState from "../components/chat/EmptyState";
import { AlertIcon, CloseIcon } from "../components/chat/icons";
import MessageList from "../components/chat/MessageList";
import { ThreadError, ThreadLoading } from "../components/chat/ThreadPlaceholders";
import { useChatStream } from "../components/chat/useChatStream";
import { useConversations } from "../components/chat/useConversations";
import { useDrawer } from "../components/chat/useDrawer";
import { useMediaQuery } from "../components/chat/useMediaQuery";
import { useModels } from "../components/chat/useModels";
import DocumentPanel from "../components/documents/DocumentPanel";

// Keep in sync with the breakpoints in chat.css.
const SIDEBAR_DRAWER_QUERY = "(max-width: 1099.98px)";
const DOCUMENTS_SLIDE_OVER_QUERY = "(max-width: 799.98px)";
const FINE_POINTER_QUERY = "(pointer: fine)";
const MAX_ID = 2_147_483_647;
const MISSING_NOTICE = "That conversation doesn't exist or has been deleted.";
const LOGOUT_FAILED = "Couldn't sign out. Check your connection and try again.";
/** A conversation created for a first message that was cut off can be committed just after the client gave up. */
const LATE_SIDEBAR_RELOAD_MS = 1500;
/** After Stop or a lost connection the server saves the partial answer's usage a moment later: check it again then. */
const LATE_USAGE_REFRESH_MS = [1500, 5000];

// Streaming re-renders the page on every frame; the document panel only depends on the selection.
const StableDocumentPanel = memo(DocumentPanel);

/** The route's conversation id; null for "/" and for anything that isn't a valid id. */
function parseConversationId(param: string | undefined): number | null {
  if (param === undefined || !/^\d{1,10}$/.test(param)) return null;
  const id = Number(param);
  return id >= 1 && id <= MAX_ID ? id : null;
}

export default function ChatPage() {
  const { conversationId: param } = useParams();
  const navigate = useNavigate();
  const { user, refresh, logout } = useAuth();
  const routeId = parseConversationId(param);
  const invalidRoute = param !== undefined && routeId === null;

  const sidebarIsDrawer = useMediaQuery(SIDEBAR_DRAWER_QUERY);
  const documentsIsSlideOver = useMediaQuery(DOCUMENTS_SLIDE_OVER_QUERY);
  const finePointer = useMediaQuery(FINE_POINTER_QUERY);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [selection, setSelection] = useState<DocumentSelection>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const sidebarId = useId();
  const documentsId = useId();
  const composerRef = useRef<ComposerHandle>(null);
  const documentsRef = useRef<HTMLDivElement>(null);
  const focusComposer = useRef(true);
  const lateReloadTimer = useRef<number | undefined>(undefined);
  const lateUsageTimers = useRef<number[]>([]);

  const conversations = useConversations();
  const models = useModels();
  const { touch, reload: reloadConversations } = conversations;
  const { selectedId: modelId, reload: reloadModels } = models;

  useEffect(
    () => () => {
      window.clearTimeout(lateReloadTimer.current);
      lateUsageTimers.current.forEach((timer) => window.clearTimeout(timer));
    },
    [],
  );

  /** Updates the sidebar's usage meter; `settled` = the answer's usage is already saved. */
  const refreshUsage = useCallback(
    (settled: boolean) => {
      const load = () => void refresh().catch(() => undefined);
      lateUsageTimers.current.forEach((timer) => window.clearTimeout(timer));
      lateUsageTimers.current = settled ? [] : LATE_USAGE_REFRESH_MS.map((ms) => window.setTimeout(load, ms));
      load();
    },
    [refresh],
  );

  const showMissing = useCallback(() => {
    setNotice(MISSING_NOTICE);
    navigate("/", { replace: true });
  }, [navigate]);

  useEffect(() => {
    if (invalidRoute) showMissing();
  }, [invalidRoute, showMissing]);

  const thread = useChatStream(routeId, {
    onConversationCreated: (id, title) => {
      touch(id, title);
      navigate(`/c/${id}`, { replace: true });
    },
    onConversationMissing: showMissing,
    onTurnFinished: (turn, settled) => {
      refreshUsage(settled);
      if (turn) {
        touch(turn.conversationId, turn.title);
        return;
      }
      // Ended before the conversation was known: the server may still have created one, possibly a moment later.
      void reloadConversations();
      window.clearTimeout(lateReloadTimer.current);
      lateReloadTimer.current = window.setTimeout(() => void reloadConversations(), LATE_SIDEBAR_RELOAD_MS);
    },
  });
  const { send, reload: reloadThread, status: threadStatus, threadKey } = thread;

  useEffect(() => {
    if (!focusComposer.current || threadStatus !== "ready") return;
    focusComposer.current = false;
    if (finePointer) composerRef.current?.focus();
  }, [threadKey, threadStatus, finePointer]);

  const sidebarDrawerOpen = sidebarIsDrawer && sidebarOpen;
  const documentsSlideOverOpen = documentsIsSlideOver && documentsOpen;
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const closeDocuments = useCallback(() => setDocumentsOpen(false), []);
  useDrawer(documentsSlideOverOpen, documentsRef, closeDocuments);

  // A drawer left open on a wider screen must not reappear when the window narrows again.
  useEffect(() => {
    if (!sidebarIsDrawer) setSidebarOpen(false);
  }, [sidebarIsDrawer]);
  useEffect(() => {
    if (!documentsIsSlideOver) setDocumentsOpen(false);
  }, [documentsIsSlideOver]);

  const handleSend = useCallback(
    async (text: string) => {
      setNotice(null);
      const accepted = await send({ message: text, modelId, documentIds: selection });
      if (!accepted) {
        composerRef.current?.restore(text);
        // The chosen model may have been disabled, deleted or withdrawn since the list was loaded (400/403): refetch
        // so the picker drops it and falls back to the default.
        reloadModels();
      }
    },
    [send, modelId, reloadModels, selection],
  );

  const startNewChat = useCallback(() => {
    setNotice(null);
    setSidebarOpen(false);
    focusComposer.current = true;
    if (routeId === null) reloadThread();
    else navigate("/");
  }, [routeId, reloadThread, navigate]);

  const onNavigate = useCallback(() => {
    setNotice(null);
    setSidebarOpen(false);
  }, []);

  const onDeleted = useCallback(
    (id: number) => {
      if (id === routeId) navigate("/", { replace: true });
    },
    [routeId, navigate],
  );

  const openChangePassword = useCallback(() => {
    setSidebarOpen(false);
    setPasswordOpen(true);
  }, []);

  const onLogout = useCallback(() => {
    // On failure the session cookie is still set, so stay signed in and say so (close the drawer to show it).
    logout().catch(() => {
      setSidebarOpen(false);
      setNotice(LOGOUT_FAILED);
    });
  }, [logout]);

  const listed = routeId === null ? undefined : conversations.conversations?.find((item) => item.id === routeId);
  const title = listed?.title ?? thread.title ?? (routeId === null ? "New chat" : "Conversation");
  const noModels = models.models !== null && models.models.length === 0;
  const selectedModel = models.models?.find((model) => model.id === modelId);
  const canCreateFiles = selectedModel?.can_create_files ?? false;
  const canCreateImages = selectedModel?.can_create_images ?? false;
  const imagesOffered = models.models?.some((model) => model.can_create_images) ?? false;
  const disabledReason =
    thread.status === "loading"
      ? "Loading conversation…"
      : thread.status === "error"
        ? "This conversation couldn't be loaded"
        : noModels
          ? "No chat models are available. Ask your administrator to enable one."
          : null;

  let body;
  if (thread.status === "loading") {
    body = <ThreadLoading />;
  } else if (thread.status === "error") {
    body = <ThreadError message={thread.loadError} onRetry={reloadThread} />;
  } else if (thread.messages.length === 0) {
    body = (
      <div className="chat-thread chat-thread-empty">
        <EmptyState
          username={user?.username ?? null}
          disabled={disabledReason !== null || thread.streaming}
          canCreateFiles={canCreateFiles}
          canCreateImages={canCreateImages}
          onPick={handleSend}
        />
      </div>
    );
  } else {
    body = (
      <MessageList
        key={thread.threadKey}
        messages={thread.messages}
        streaming={thread.streaming}
        onRetry={handleSend}
      />
    );
  }

  return (
    <div className="chat-page">
      <ConversationSidebar
        id={sidebarId}
        conversations={conversations}
        activeId={routeId}
        user={user}
        imagesOffered={imagesOffered}
        drawer={sidebarIsDrawer}
        open={sidebarDrawerOpen}
        onClose={closeSidebar}
        onNavigate={onNavigate}
        onNewChat={startNewChat}
        onDeleted={onDeleted}
        onChangePassword={openChangePassword}
        onLogout={onLogout}
      />

      <main className="chat-main" inert={sidebarDrawerOpen || documentsSlideOverOpen}>
        <ChatHeader
          title={title}
          models={models}
          sidebarToggle={
            sidebarIsDrawer
              ? { open: sidebarDrawerOpen, controls: sidebarId, onToggle: () => setSidebarOpen((open) => !open) }
              : null
          }
          documentsToggle={
            documentsIsSlideOver
              ? { open: documentsSlideOverOpen, controls: documentsId, onToggle: () => setDocumentsOpen((open) => !open) }
              : null
          }
        />
        {notice ? (
          <div className="chat-notice" role="status">
            <span>{notice}</span>
            <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={() => setNotice(null)} aria-label="Dismiss">
              <CloseIcon size={14} />
            </button>
          </div>
        ) : null}

        {body}

        <div className="chat-bottom">
          {thread.sendError ? (
            <div className="chat-banner" role="alert">
              <AlertIcon size={15} />
              <span className="chat-banner-text">{thread.sendError}</span>
              <button
                type="button"
                className="btn btn-ghost btn-icon btn-sm"
                onClick={thread.dismissSendError}
                aria-label="Dismiss error"
              >
                <CloseIcon size={14} />
              </button>
            </div>
          ) : null}
          <Composer
            ref={composerRef}
            streaming={thread.streaming}
            disabledReason={disabledReason}
            selection={selection}
            canCreateFiles={canCreateFiles}
            canCreateImages={canCreateImages}
            onSend={handleSend}
            onStop={thread.stop}
            onScopeClick={documentsIsSlideOver ? () => setDocumentsOpen(true) : undefined}
          />
        </div>
      </main>

      <div
        ref={documentsRef}
        id={documentsId}
        className={documentsSlideOverOpen ? "chat-documents chat-documents-open" : "chat-documents"}
        role={documentsSlideOverOpen ? "dialog" : undefined}
        aria-modal={documentsSlideOverOpen || undefined}
        aria-label={documentsSlideOverOpen ? "Documents" : undefined}
        tabIndex={-1}
        inert={sidebarDrawerOpen}
      >
        <StableDocumentPanel
          selection={selection}
          onSelectionChange={setSelection}
          onClose={documentsIsSlideOver ? closeDocuments : undefined}
        />
      </div>

      {sidebarDrawerOpen || documentsSlideOverOpen ? (
        <div
          className="chat-backdrop"
          aria-hidden="true"
          onClick={() => {
            closeSidebar();
            closeDocuments();
          }}
        />
      ) : null}
      {passwordOpen ? <ChangePasswordModal onClose={() => setPasswordOpen(false)} /> : null}
    </div>
  );
}
