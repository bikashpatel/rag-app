import { memo, useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { DocumentConfig, DocumentSelection } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import PendingCheckoutBanner from "../components/billing/PendingCheckoutBanner";
import PlanBanner from "../components/billing/PlanBanner";
import PlanEndedPanel from "../components/billing/PlanEndedPanel";
import { canUpgrade, checkoutPath } from "../components/billing/planText";
import "../components/chat/chat.css";
import Composer, { type ComposerHandle } from "../components/chat/Composer";
import ConversationSidebar from "../components/chat/ConversationSidebar";
import EmptyState from "../components/chat/EmptyState";
import MessageList from "../components/chat/MessageList";
import PlusMenu from "../components/chat/PlusMenu";
import { ThreadError, ThreadLoading } from "../components/chat/ThreadPlaceholders";
import TopBar from "../components/chat/TopBar";
import { useAttachments } from "../components/chat/useAttachments";
import { useChatStream } from "../components/chat/useChatStream";
import { useConversations } from "../components/chat/useConversations";
import { useDrawer } from "../components/chat/useDrawer";
import { useMediaQuery } from "../components/chat/useMediaQuery";
import { useModels } from "../components/chat/useModels";
import { useStreamNote } from "../components/chat/useStreamNote";
import GmailImportModal from "../components/connections/GmailImportModal";
import {
  gmailReady,
  onPluginBrowserRequest,
  requestConnections,
  usePluginLifecycle,
  usePlugins,
} from "../components/connections/pluginStore";
import DocumentPanel from "../components/documents/DocumentPanel";
import { AlertCircleIcon, AlertIcon, CloseIcon, InfoIcon, LockIcon } from "../components/ui/icons";
import { onConversationsDeleted } from "../lib/appEvents";
import { formatNumber } from "../lib/format";
import { useOpenSettings, useReplacePath, type SettingsTab } from "../lib/settingsLink";

// Keep in sync with the breakpoints in chat.css.
const SIDEBAR_DRAWER_QUERY = "(max-width: 1099.98px)";
const DOCUMENTS_SLIDE_OVER_QUERY = "(max-width: 799.98px)";
const FINE_POINTER_QUERY = "(pointer: fine)";
/** "hidden" while the documents column is collapsed on this device (800 px and wider; functional brief 8.4). */
const DOCUMENTS_PANEL_KEY = "rag.documentsPanel";
const MAX_ID = 2_147_483_647;
const APP_TITLE = "RAG Assistant";
const MISSING_NOTICE = "That conversation doesn't exist or has been deleted.";
const LOGOUT_FAILED = "Couldn't sign out. Check your connection and try again.";
const UPLOAD_SETTINGS_FAILED = "Couldn't check your upload settings, so nothing was attached. Try again.";
/** A conversation created for a first message that was cut off can be committed just after the client gave up. */
const LATE_SIDEBAR_RELOAD_MS = 1500;
/** After Stop or a lost connection the server saves the partial answer's usage a moment later: check it again then. */
const LATE_USAGE_REFRESH_MS = [1500, 5000];
/** The unsent message when the plan ended on the page: put back when that chat (or a new chat) opens with access. */
const PLAN_DRAFT_KEY = "rag.planDraft";

type PlanDraft = { conversation_id: number | null; text: string };

// Streaming re-renders the page on every frame; the document panel only depends on the selection.
const StableDocumentPanel = memo(DocumentPanel);

type Notice = { text: string; tone: "info" | "danger" };

/** The route's conversation id; null for "/" and for anything that isn't a valid id. */
function parseConversationId(param: string | undefined): number | null {
  if (param === undefined || !/^\d{1,10}$/.test(param)) return null;
  const id = Number(param);
  return id >= 1 && id <= MAX_ID ? id : null;
}

function readDocumentsCollapsed(): boolean {
  try {
    return localStorage.getItem(DOCUMENTS_PANEL_KEY) === "hidden";
  } catch {
    return false;
  }
}

function storeDocumentsCollapsed(collapsed: boolean): void {
  try {
    if (collapsed) localStorage.setItem(DOCUMENTS_PANEL_KEY, "hidden");
    else localStorage.removeItem(DOCUMENTS_PANEL_KEY);
  } catch {
    /* storage unavailable: the choice lasts until the page reloads */
  }
}

function readPlanDraft(): PlanDraft | null {
  try {
    const draft = JSON.parse(sessionStorage.getItem(PLAN_DRAFT_KEY) ?? "null") as PlanDraft | null;
    return draft !== null && typeof draft.text === "string" ? draft : null;
  } catch {
    return null;
  }
}

function storePlanDraft(draft: PlanDraft | null): void {
  try {
    if (draft) sessionStorage.setItem(PLAN_DRAFT_KEY, JSON.stringify(draft));
    else sessionStorage.removeItem(PLAN_DRAFT_KEY);
  } catch {
    /* storage unavailable: the draft is lost with the page */
  }
}

export default function ChatPage() {
  const { conversationId: param } = useParams();
  const navigate = useNavigate();
  const replacePath = useReplacePath();
  const openSettingsAt = useOpenSettings();
  const { user, refresh, logout, preferences } = useAuth();
  const routeId = parseConversationId(param);
  const invalidRoute = param !== undefined && routeId === null;

  const sidebarIsDrawer = useMediaQuery(SIDEBAR_DRAWER_QUERY);
  const documentsIsSlideOver = useMediaQuery(DOCUMENTS_SLIDE_OVER_QUERY);
  const finePointer = useMediaQuery(FINE_POINTER_QUERY);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [documentsCollapsed, setDocumentsCollapsed] = useState(readDocumentsCollapsed);
  const [gmailBrowserOpen, setGmailBrowserOpen] = useState(false);
  const [selection, setSelection] = useState<DocumentSelection>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const sidebarId = useId();
  const documentsId = useId();
  const composerRef = useRef<ComposerHandle>(null);
  const plusRef = useRef<HTMLButtonElement>(null);
  const documentsRef = useRef<HTMLDivElement>(null);
  const focusComposer = useRef(true);
  const lateReloadTimer = useRef<number | undefined>(undefined);
  const lateUsageTimers = useRef<number[]>([]);
  const routeIdRef = useRef(routeId);
  routeIdRef.current = routeId;
  /** The composer's unsent text (kept when the plan ends on this page). */
  const draftRef = useRef("");
  const [draftKept, setDraftKept] = useState(false);

  const conversations = useConversations();
  const models = useModels(preferences.default_model_id);
  const attachments = useAttachments(user?.id ?? null);
  const { touch, reload: reloadConversations, clearAll: clearConversations } = conversations;
  const { selectedId: modelId, reload: reloadModels, resetToDefault: resetModel } = models;
  const { add: addAttachments, take: takeAttachments, restore: restoreAttachments } = attachments;
  usePluginLifecycle();
  const { plugins } = usePlugins();
  const gmail = gmailReady(plugins);
  const gmailUsable = gmail !== null;

  const openSettings = useCallback(
    (tab: SettingsTab, focusPluginId?: string) => {
      setSidebarOpen(false);
      openSettingsAt(tab, focusPluginId ? { focusPluginId } : {});
    },
    [openSettingsAt],
  );

  // Settings → Plugins & data → Browse Gmail asks for the Gmail browser once its dialog has closed (U22).
  useEffect(() => onPluginBrowserRequest((pluginId) => setGmailBrowserOpen(pluginId === "gmail")), []);

  // The Gmail browser closes by itself when Gmail stops being usable (turned off, disconnected, reconnect needed,
  // access removed: often a search or import that got a 403/409, which reloads the list). Settings → Plugins & data
  // then says why and offers Reconnect, instead of the dialog just vanishing. A list not loaded yet decides nothing.
  useEffect(() => {
    if (gmailUsable || !gmailBrowserOpen || plugins === null) return;
    setGmailBrowserOpen(false);
    requestConnections("gmail");
  }, [gmailUsable, gmailBrowserOpen, plugins]);

  // The Gmail browser returns focus to its opener; when that is gone (Settings' Browse button), use the "+" button.
  const gmailWasOpen = useRef(false);
  useEffect(() => {
    if (gmailBrowserOpen) {
      gmailWasOpen.current = true;
      return;
    }
    if (!gmailWasOpen.current) return;
    gmailWasOpen.current = false;
    const frame = window.requestAnimationFrame(() => {
      const active = document.activeElement;
      if (!active || active === document.body) plusRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [gmailBrowserOpen]);

  useEffect(
    () => () => {
      window.clearTimeout(lateReloadTimer.current);
      lateUsageTimers.current.forEach((timer) => window.clearTimeout(timer));
    },
    [],
  );

  /** Updates the avatar menu's usage meters; `settled` = the answer's usage is already saved. */
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
    setNotice({ text: MISSING_NOTICE, tone: "info" });
    replacePath("/");
  }, [replacePath]);

  useEffect(() => {
    if (invalidRoute) showMissing();
  }, [invalidRoute, showMissing]);

  const thread = useChatStream(routeId, {
    onConversationCreated: (id, title) => {
      touch(id, title);
      // Through useReplacePath, so Settings opened while the first answer streams stays open (U26).
      replacePath(`/c/${id}`);
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
  const { send, stop, reload: reloadThread, status: threadStatus, threadKey } = thread;
  const threadEmpty = thread.messages.length === 0;

  // The plan ended while this page was open (8.7): the page stays, an answer streaming finishes, then the composer
  // becomes the plan-ended panel and its unsent text is kept for when the plan is renewed.
  const hasAccess = user === null || user.is_admin || user.plan.access;
  const planEnded = !hasAccess && !thread.streaming;
  const keepDraft = useCallback(() => {
    if (!draftRef.current.trim()) return;
    storePlanDraft({ conversation_id: routeIdRef.current, text: draftRef.current });
    setDraftKept(true);
  }, []);
  useEffect(() => {
    if (planEnded) keepDraft();
  }, [planEnded, keepDraft]);
  useEffect(() => {
    if (!hasAccess) return;
    const draft = readPlanDraft();
    if (!draft || (routeId !== null && draft.conversation_id !== routeId)) return;
    composerRef.current?.restore(draft.text);
    storePlanDraft(null);
    setDraftKept(false);
  }, [hasAccess, routeId]);

  useEffect(() => {
    if (!focusComposer.current || threadStatus !== "ready") return;
    focusComposer.current = false;
    // Never out of a menu opened meanwhile (the "+" menu right after New chat would lose its keyboard focus).
    if (finePointer && !document.activeElement?.closest('[role="menu"]')) composerRef.current?.focus();
  }, [threadKey, threadStatus, finePointer]);

  // The account default changed in Settings while this chat has no messages: the picker follows it (8.2).
  const threadEmptyRef = useRef(threadEmpty);
  threadEmptyRef.current = threadEmpty;
  useEffect(() => {
    if (threadEmptyRef.current) resetModel();
  }, [preferences.default_model_id, resetModel]);

  // Settings → Delete all chats: the list empties, a stream stops, and a conversation route goes to "/" (Settings
  // stays open: useReplacePath).
  useEffect(
    () =>
      onConversationsDeleted(() => {
        clearConversations();
        stop();
        if (routeIdRef.current !== null) replacePath("/");
        else reloadThread();
      }),
    [clearConversations, stop, replacePath, reloadThread],
  );

  const sidebarDrawerOpen = sidebarIsDrawer && sidebarOpen;
  const documentsSlideOverOpen = documentsIsSlideOver && documentsOpen;
  const documentsHidden = !documentsIsSlideOver && documentsCollapsed;
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

  const setCollapsed = useCallback((collapsed: boolean) => {
    storeDocumentsCollapsed(collapsed);
    setDocumentsCollapsed(collapsed);
  }, []);

  const toggleDocuments = useCallback(() => {
    if (documentsIsSlideOver) setDocumentsOpen((open) => !open);
    else setCollapsed(!documentsCollapsed);
  }, [documentsIsSlideOver, documentsCollapsed, setCollapsed]);

  /** Sends a message; attached files go with it (ready ones join an explicit selection) unless it is a Retry. */
  const sendMessage = useCallback(
    async (text: string, withAttachments: boolean) => {
      setNotice(null);
      const taken = withAttachments ? takeAttachments() : { documentIds: [], snapshot: [] };
      let documentIds = selection;
      if (selection !== null && taken.documentIds.length > 0) {
        documentIds = [...selection, ...taken.documentIds.filter((id) => !selection.includes(id))];
        setSelection(documentIds);
      }
      const accepted = await send({ message: text, modelId, documentIds });
      if (!accepted) {
        if (!draftRef.current.trim()) draftRef.current = text;
        // The plan-ended panel may already have replaced the composer: keep the text for the renewal instead.
        if (composerRef.current) composerRef.current.restore(text);
        else keepDraft();
        restoreAttachments(taken.snapshot);
        // The chosen model may have been disabled, deleted or withdrawn since the list was loaded (400/403): refetch
        // so the picker drops it and falls back to the default.
        reloadModels();
      }
    },
    [send, modelId, reloadModels, selection, takeAttachments, restoreAttachments, keepDraft],
  );
  const handleSend = useCallback((text: string) => void sendMessage(text, true), [sendMessage]);
  const handleRetry = useCallback((text: string) => void sendMessage(text, false), [sendMessage]);

  const attachFiles = useCallback(
    async (files: File[], folders: string[], config: DocumentConfig | null) => {
      let settings = config;
      if (!settings) {
        try {
          settings = await api.get<DocumentConfig>("/documents/config");
        } catch {
          setNotice({ text: UPLOAD_SETTINGS_FAILED, tone: "danger" });
          return;
        }
      }
      addAttachments(files, folders, settings);
    },
    [addAttachments],
  );

  const startNewChat = useCallback(() => {
    setNotice(null);
    setSidebarOpen(false);
    focusComposer.current = true;
    resetModel();
    if (routeId === null) reloadThread();
    else navigate("/");
  }, [routeId, reloadThread, navigate, resetModel]);

  const onNavigate = useCallback(() => {
    setNotice(null);
    setSidebarOpen(false);
  }, []);

  const onDeleted = useCallback(
    (id: number) => {
      if (id === routeIdRef.current) replacePath("/");
    },
    [replacePath],
  );

  const onLogout = useCallback(() => {
    // On failure the session cookie is still set, so stay signed in and say so.
    logout().catch(() => {
      setSidebarOpen(false);
      setNotice({ text: LOGOUT_FAILED, tone: "danger" });
    });
  }, [logout]);

  const onPreferenceError = useCallback((text: string) => setNotice({ text, tone: "danger" }), []);

  const streamNote = useStreamNote(thread.threadKey, thread.streaming, thread.messages);

  const listed = routeId === null ? undefined : conversations.conversations?.find((item) => item.id === routeId);
  const knownTitle = listed?.title ?? thread.title;
  const title = knownTitle ?? (routeId === null ? "New chat" : "Conversation");

  useEffect(() => {
    const previous = document.title;
    document.title = knownTitle ? `${knownTitle} · ${APP_TITLE}` : APP_TITLE;
    return () => {
      document.title = previous;
    };
  }, [knownTitle]);

  const noModels = models.models !== null && models.models.length === 0;
  const selectedModel = models.models?.find((model) => model.id === modelId) ?? null;
  // A capability counts when the model and the server can and the plan includes it (8.6).
  const plan = user?.plan ?? null;
  const canCreateFiles = (selectedModel?.can_create_files ?? false) && (plan?.features.files ?? true);
  const canCreateImages = (selectedModel?.can_create_images ?? false) && (plan?.features.images ?? true);
  const imagesOffered = (models.models?.some((model) => model.can_create_images) ?? false) && (plan?.features.images ?? true);
  const imageLimit = user && !user.is_admin ? user.limits.monthly_image_limit : 0;
  const imagesLeft = user && imageLimit > 0 ? Math.max(imageLimit - user.usage.images, 0) : null;
  const imagesNote =
    imagesLeft !== null ? `${formatNumber(imagesLeft)} of ${formatNumber(imageLimit)} left this period` : "Saved to My documents";
  const pendingOrder = user && !user.is_admin ? user.plan.pending_order : null;
  const sendError = thread.sendError;
  const planError = sendError?.code === "feature_not_in_plan" || sendError?.code === "allowance_used";
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
  } else if (threadEmpty) {
    body = (
      <div className="chat-thread chat-thread-empty">
        <EmptyState
          username={user?.username ?? null}
          disabled={disabledReason !== null || thread.streaming || attachments.waiting !== null}
          canCreateFiles={canCreateFiles}
          canCreateImages={canCreateImages && imagesLeft !== 0}
          selection={selection}
          imagesNote={imagesNote}
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
        onRetry={handleRetry}
      />
    );
  }

  // The scope chip opens the slide-over on phones and expands a collapsed column on wider screens.
  const onScopeClick = documentsIsSlideOver
    ? () => setDocumentsOpen(true)
    : documentsCollapsed
      ? () => setCollapsed(false)
      : undefined;

  return (
    <div className={documentsHidden ? "chat-page chat-page-docs-collapsed" : "chat-page"}>
      <ConversationSidebar
        id={sidebarId}
        conversations={conversations}
        activeId={routeId}
        drawer={sidebarIsDrawer}
        open={sidebarDrawerOpen}
        onClose={closeSidebar}
        onNavigate={onNavigate}
        onNewChat={startNewChat}
        onDeleted={onDeleted}
      />

      <main className="chat-main" inert={sidebarDrawerOpen || documentsSlideOverOpen}>
        <TopBar
          title={title}
          user={user}
          models={models}
          imagesOffered={imagesOffered}
          sidebarToggle={
            sidebarIsDrawer
              ? { open: sidebarDrawerOpen, controls: sidebarId, onToggle: () => setSidebarOpen((open) => !open) }
              : null
          }
          documentsToggle={{
            open: documentsIsSlideOver ? documentsSlideOverOpen : !documentsCollapsed,
            controls: documentsId,
            onToggle: toggleDocuments,
          }}
          openSettings={openSettings}
          onPreferenceError={onPreferenceError}
          onLogout={onLogout}
        />
        {pendingOrder ? (
          <PendingCheckoutBanner order={pendingOrder} />
        ) : user && !user.is_admin ? (
          <PlanBanner key={user.plan.paid_through ?? ""} plan={user.plan} />
        ) : null}
        {notice ? (
          <div
            className={`ui-notice ui-notice-${notice.tone} chat-notice`}
            role={notice.tone === "danger" ? "alert" : "status"}
          >
            {notice.tone === "danger" ? <AlertCircleIcon size={15} /> : <InfoIcon size={15} />}
            <span className="ui-notice-text">{notice.text}</span>
            <button type="button" className="btn btn-ghost btn-icon" onClick={() => setNotice(null)} aria-label="Dismiss">
              <CloseIcon size={15} />
            </button>
          </div>
        ) : null}

        {body}
        <div className="sr-only" role="status" aria-live="polite">
          {streamNote}
        </div>

        <div className="chat-bottom">
          {sendError ? (
            <div className={`ui-notice ui-notice-${planError ? "warning" : "danger"} chat-banner`} role="alert">
              {sendError.code === "feature_not_in_plan" ? (
                <LockIcon size={15} />
              ) : planError ? (
                <AlertIcon size={15} />
              ) : (
                <AlertCircleIcon size={15} />
              )}
              <span className="ui-notice-text">{sendError.message}</span>
              {planError ? (
                <span className="billing-notice-actions">
                  {sendError.code === "feature_not_in_plan" ? (
                    <Link className="btn btn-sm" to="/plans">
                      See plans
                    </Link>
                  ) : null}
                  {sendError.code === "allowance_used" && plan?.can_start_new_period && plan.plan ? (
                    <Link className="btn btn-primary btn-sm" to={checkoutPath(plan.plan.code, "now")}>
                      Start a new period now
                    </Link>
                  ) : null}
                  {sendError.code === "allowance_used" && plan && canUpgrade(plan) ? (
                    <Link className="btn btn-sm" to="/plans">
                      Upgrade
                    </Link>
                  ) : null}
                </span>
              ) : null}
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                onClick={thread.dismissSendError}
                aria-label="Dismiss error"
              >
                <CloseIcon size={15} />
              </button>
            </div>
          ) : null}
          {planEnded && user ? (
            <PlanEndedPanel plan={user.plan} draftKept={draftKept} />
          ) : (
            <Composer
              ref={composerRef}
              streaming={thread.streaming}
              disabledReason={disabledReason}
              replying={!threadEmpty}
              selection={selection}
              canCreateFiles={canCreateFiles}
              canCreateImages={canCreateImages}
              enterToSend={preferences.enter_to_send}
              attachments={attachments.views}
              onRemoveAttachment={attachments.remove}
              waiting={attachments.waiting}
              onDropFiles={(files, folders) => void attachFiles(files, folders, null)}
              plusMenu={
                <PlusMenu
                  triggerRef={plusRef}
                  user={user}
                  models={models.models}
                  selectedModel={selectedModel}
                  composerDisabled={disabledReason}
                  plugins={plugins}
                  onStarter={(prefix) => composerRef.current?.insertStarter(prefix)}
                  onAttach={(files, config) => void attachFiles(files, [], config)}
                  onImportGmail={() => setGmailBrowserOpen(true)}
                  onOpenPluginSettings={(pluginId) => openSettings("plugins", pluginId)}
                />
              }
              onSend={handleSend}
              onStop={thread.stop}
              onScopeClick={onScopeClick}
              onDraftChange={(text) => {
                draftRef.current = text;
              }}
            />
          )}
        </div>
      </main>

      {/* Collapsed on wider screens it stays mounted (only hidden): it keeps polling and publishing its list, which
          the attachment chips and the file cards read (8.4). */}
      <div
        ref={documentsRef}
        id={documentsId}
        className={documentsSlideOverOpen ? "chat-documents chat-documents-open" : "chat-documents"}
        role={documentsSlideOverOpen ? "dialog" : undefined}
        aria-modal={documentsSlideOverOpen || undefined}
        aria-label={documentsSlideOverOpen ? "Documents" : undefined}
        tabIndex={-1}
        hidden={documentsHidden}
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
      {gmailBrowserOpen && gmail ? <GmailImportModal plugin={gmail} onClose={() => setGmailBrowserOpen(false)} /> : null}
    </div>
  );
}
