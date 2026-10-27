import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent, type ReactNode, type RefObject } from "react";
import { api } from "../../api/client";
import type { ChatModel, DocumentConfig, Me, PluginInfo } from "../../api/types";
import { formatNumber } from "../../lib/format";
import LockedMenuItem from "../billing/LockedHint";
import { isStacked, istDayMonth } from "../billing/planText";
import { pluginStatus } from "../connections/pluginStore";
import { BYTES_PER_MB } from "../documents/fileTypes";
import Menu, { MenuGroup, MenuItem } from "../ui/Menu";
import {
  ChevronRightIcon,
  FileIcon,
  ImageIcon,
  MailIcon,
  PaperclipIcon,
  PenIcon,
  PlusIcon,
  SearchIcon,
  UploadIcon,
} from "../ui/icons";
import { STARTERS } from "./starters";

// The composer's "+" menu (functional brief 9.1, visual spec 6.15). Items that don't exist for the user are hidden;
// items that are only unavailable now stay, disabled, with the reason as their visible description (U9). A feature the
// server offers but the plan doesn't include is one locked item that leads to the plans (plans-billing.md 8.6).

/** An image needs a document slot and about this much storage; a created file, this much (9.1). */
const IMAGE_STORAGE_BYTES = 8 * BYTES_PER_MB;
const FILE_STORAGE_BYTES = 256 * 1024;
const CHECKING = "Checking your storage…";

const FORMATS = [
  { id: "pdf", short: "PDF", name: "PDF document", tone: "red" },
  { id: "docx", short: "Word", name: "Word document", tone: "blue" },
  { id: "xlsx", short: "Excel", name: "Excel spreadsheet", tone: "green" },
  { id: "md", short: "Markdown", name: "Markdown file", tone: "ink" },
] as const;

type ConfigState = { kind: "idle" } | { kind: "loading" } | { kind: "failed" } | { kind: "ready"; config: DocumentConfig };

type PlusMenuProps = {
  triggerRef: RefObject<HTMLButtonElement | null>;
  user: Me | null;
  models: ChatModel[] | null;
  selectedModel: ChatModel | null;
  /** Why the composer can't be used now (no models, the conversation is loading or failed), or null. */
  composerDisabled: string | null;
  plugins: PluginInfo[] | null;
  onStarter: (prefix: string) => void;
  /** Picked files; `config` null when the upload settings couldn't be loaded (the caller loads them). */
  onAttach: (files: File[], config: DocumentConfig | null) => void;
  onImportGmail: () => void;
  onOpenPluginSettings: (pluginId: string) => void;
};

type Room = { slots: number; storage: number };

function roomLeft(config: DocumentConfig): Room {
  return {
    slots: config.max_documents > 0 ? config.max_documents - config.document_count : Infinity,
    storage: config.max_storage_mb > 0 ? config.max_storage_mb * BYTES_PER_MB - config.storage_bytes : Infinity,
  };
}

/** Why a created document wouldn't fit, or null (also null when the settings couldn't be loaded: the server decides). */
function storageReason(state: ConfigState, needs: number): string | null {
  if (state.kind === "loading" || state.kind === "idle") return CHECKING;
  if (state.kind === "failed") return null;
  const room = roomLeft(state.config);
  return room.slots <= 0 || room.storage < needs ? "Your documents are full." : null;
}

function capability(plugin: PluginInfo, id: string) {
  return plugin.capabilities.find((item) => item.id === id);
}

/** "You've used this period's 30 images." and when the count starts again (8.9). */
function imagesUsedUp(user: Me, limit: number): string {
  const used = `You've used this period's ${formatNumber(limit)} images.`;
  const { plan, usage } = user;
  if (plan.open_ended || plan.period_end === null) return `${used} The count starts again on ${istDayMonth(usage.window_end)}.`;
  if (isStacked(plan) || plan.next_plan !== null) {
    return `${used} The count starts again with your next period on ${istDayMonth(usage.window_end)}.`;
  }
  return plan.valid_through ? `${used} Your plan is valid through ${istDayMonth(plan.valid_through)}.` : used;
}

export default function PlusMenu({
  triggerRef,
  user,
  models,
  selectedModel,
  composerDisabled,
  plugins,
  onStarter,
  onAttach,
  onImportGmail,
  onOpenPluginSettings,
}: PlusMenuProps) {
  const [configState, setConfigState] = useState<ConfigState>({ kind: "idle" });
  /** The menu opens 8 px above the whole composer, not over its input (visual spec 6.15). */
  const [offset, setOffset] = useState(8);
  const lastConfig = useRef<DocumentConfig | null>(null);
  const controller = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const docTitleId = useId();
  const docReasonId = useId();

  useEffect(() => () => controller.current?.abort(), []);

  // GET /documents/config every time the menu opens (aborted when it closes): the counts change with every upload.
  const onOpenChange = useCallback(
    (open: boolean) => {
      controller.current?.abort();
      controller.current = null;
      if (!open) return;
      // Batched with the open state, so the first placement already uses it.
      const trigger = triggerRef.current;
      const composer = trigger?.closest(".chat-composer");
      if (trigger && composer) {
        const above = trigger.getBoundingClientRect().top - composer.getBoundingClientRect().top;
        setOffset(Math.max(8, Math.round(above) + 8));
      }
      const request = new AbortController();
      controller.current = request;
      setConfigState({ kind: "loading" });
      api.get<DocumentConfig>("/documents/config", { signal: request.signal }).then(
        (config) => {
          lastConfig.current = config;
          setConfigState({ kind: "ready", config });
        },
        () => {
          if (!request.signal.aborted) setConfigState({ kind: "failed" });
        },
      );
    },
    [triggerRef],
  );

  function onFilesPicked(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // picking the same file again fires change again
    if (files.length > 0) onAttach(files, lastConfig.current);
  }

  const modelName = selectedModel?.display_name ?? "This model";
  const modelMissing = selectedModel ? null : models === null ? "Loading models…" : "Pick a model first.";

  // Attach files
  let attachReason = composerDisabled;
  if (!attachReason && configState.kind !== "failed") {
    if (configState.kind !== "ready") attachReason = CHECKING;
    else {
      const { config } = configState;
      const room = roomLeft(config);
      if (room.slots <= 0) attachReason = `You've reached your limit of ${formatNumber(config.max_documents)} documents.`;
      else if (room.storage <= 0) attachReason = "Your storage is full.";
    }
  }
  const config = configState.kind === "ready" ? configState.config : lastConfig.current;
  const attachDescription = config ? `Upload to My documents · up to ${config.max_upload_mb} MB each` : "Upload to My documents";

  // Create an image: offered by the server; in the plan, or one locked item naming the plan that has it.
  const plan = user?.plan ?? null;
  const imagesLocked = plan !== null && !plan.features.images ? plan.unlock.images : null;
  const imagesOffered = (models?.some((model) => model.can_create_images) ?? false) && (plan?.features.images ?? true);
  const imagesShown = (models?.some((model) => model.can_create_images) ?? false) && (imagesOffered || imagesLocked !== null);
  const imageLimit = user && !user.is_admin ? user.limits.monthly_image_limit : 0;
  const imagesUsed = user?.usage.images ?? 0;
  const imageReason =
    composerDisabled ??
    modelMissing ??
    (selectedModel && !selectedModel.can_create_images
      ? `${modelName} can't create images. Pick a model marked Images.`
      : null) ??
    (user && imageLimit > 0 && imagesUsed >= imageLimit ? imagesUsedUp(user, imageLimit) : null) ??
    storageReason(configState, IMAGE_STORAGE_BYTES);
  const imageDescription =
    imageLimit > 0
      ? `${formatNumber(Math.max(imageLimit - imagesUsed, 0))} of ${formatNumber(imageLimit)} left this period`
      : "Saved to My documents";

  // Create a document
  const filesLocked = plan !== null && !plan.features.files ? plan.unlock.files : null;
  const filesServed = models?.some((model) => model.can_create_files) ?? false;
  const filesOffered = filesServed && (plan?.features.files ?? true);
  const filesShown = filesServed && (filesOffered || filesLocked !== null);
  const filesReason =
    composerDisabled ??
    modelMissing ??
    (selectedModel && !selectedModel.can_create_files ? `${modelName} can't create files. Pick a model marked Files.` : null) ??
    storageReason(configState, FILE_STORAGE_BYTES);

  // Gmail: hidden unless set up and turned on; one locked item outside the plan; one Connect or Reconnect item until
  // connected.
  const gmail = plugins?.find((plugin) => plugin.id === "gmail") ?? null;
  const gmailStatus = gmail ? pluginStatus(gmail) : null;
  const gmailLocked = gmailStatus === "not_in_plan" ? (plan?.unlock.plugins.gmail ?? null) : null;
  const gmailShown =
    gmail !== null && gmailStatus !== "not_configured" && gmailStatus !== "disabled" && gmailStatus !== "not_in_plan";
  const read = gmail ? capability(gmail, "read") : undefined;
  const draft = gmail ? capability(gmail, "draft") : undefined;
  const chatReason = (granted: boolean | undefined, what: string): string | null =>
    composerDisabled ??
    (gmail?.connection && !gmail.connection.chat_enabled
      ? "Gmail is off in chat. Turn on “Use in chat” in Settings → Plugins & data."
      : null) ??
    (!granted ? `Grant Gmail ${what} access in Settings → Plugins & data.` : null) ??
    modelMissing ??
    (selectedModel && !selectedModel.supports_tools ? `${modelName} can't use Gmail. Pick another model.` : null);
  const connected = gmailStatus === "connected";
  const showSearch = connected && read?.enabled === true;
  const showDraft = connected && draft?.enabled === true;
  const showImport = connected && read?.enabled === true && gmail?.supports_import === true;

  return (
    <>
      <Menu
        label="Add files and more"
        placement="top-start"
        offset={offset}
        className="chat-plus-menu"
        triggerRef={triggerRef}
        onOpenChange={onOpenChange}
        trigger={(props) => (
          <button {...props} type="button" className="chat-plus" aria-label="Add files and more" title="Add files and more">
            <PlusIcon size={20} />
          </button>
        )}
      >
        <MenuItem
          icon={
            <span className="ui-menu-icon">
              <PaperclipIcon size={18} />
            </span>
          }
          description={attachReason ?? attachDescription}
          disabled={attachReason !== null}
          // Synchronous, inside the activating click or keydown: Safari opens a file picker only in a user gesture.
          onSelect={() => inputRef.current?.click()}
        >
          Attach files…
        </MenuItem>

        {imagesShown || filesShown ? (
          <MenuGroup label="Create">
            {imagesShown && !imagesOffered && imagesLocked ? (
              <LockedMenuItem icon={<ImageIcon size={18} />} plan={imagesLocked}>
                Create an image
              </LockedMenuItem>
            ) : null}
            {imagesOffered ? (
              <MenuItem
                icon={
                  <span className="ui-menu-icon">
                    <ImageIcon size={18} />
                  </span>
                }
                description={imageReason ?? imageDescription}
                disabled={imageReason !== null}
                onSelect={() => onStarter(STARTERS.image)}
              >
                Create an image
              </MenuItem>
            ) : null}
            {filesShown && !filesOffered && filesLocked ? (
              <LockedMenuItem icon={<FileIcon size={18} />} plan={filesLocked}>
                Create a document
              </LockedMenuItem>
            ) : null}
            {filesOffered ? (
              <>
                {/* Not an item: the visual label of the format chips' group (U29). */}
                <div className="chat-plus-doc-row" aria-hidden="true">
                  <span className="ui-menu-icon">
                    <FileIcon size={18} />
                  </span>
                  <span className="ui-menu-text">
                    <span id={docTitleId} className="chat-plus-doc-title">
                      Create a document
                    </span>
                    <span id={docReasonId} className="ui-menu-desc">
                      {filesReason ?? "Pick a format to start the prompt"}
                    </span>
                  </span>
                </div>
                <MenuGroup labelledBy={docTitleId} orientation="horizontal" className="chat-plus-formats">
                  {FORMATS.map((format) => (
                    <MenuItem
                      key={format.id}
                      className="chat-plus-format"
                      label={format.name}
                      icon={<span className={`chat-plus-format-dot chat-plus-format-${format.tone}`} aria-hidden="true" />}
                      describedBy={filesReason ? docReasonId : undefined}
                      disabled={filesReason !== null}
                      onSelect={() => onStarter(STARTERS[format.id])}
                    >
                      {format.short}
                    </MenuItem>
                  ))}
                </MenuGroup>
              </>
            ) : null}
          </MenuGroup>
        ) : null}

        {gmailLocked ? (
          <MenuGroup label="Gmail">
            <LockedMenuItem icon={<MailIcon size={18} />} iconClassName="chat-plus-gmail" plan={gmailLocked}>
              Search, draft and import email
            </LockedMenuItem>
          </MenuGroup>
        ) : null}
        {gmailShown && gmail ? (
          gmailStatus === "available" || gmailStatus === "reconnect_needed" ? (
            <MenuGroup label="Gmail">
              <MenuItem
                icon={
                  <span className="ui-menu-icon chat-plus-gmail">
                    <MailIcon size={18} />
                  </span>
                }
                description={
                  gmailStatus === "reconnect_needed"
                    ? (gmail.connection?.status_message ?? "Gmail needs to be reconnected.")
                    : "To search, draft and import email"
                }
                end={<ChevronRightIcon size={16} />}
                onSelect={() => onOpenPluginSettings(gmail.id)}
              >
                {gmailStatus === "reconnect_needed" ? "Reconnect Gmail…" : "Connect Gmail…"}
              </MenuItem>
            </MenuGroup>
          ) : showSearch || showDraft || showImport ? (
            <MenuGroup label="Gmail">
              {showSearch ? (
                <GmailItem
                  icon={<SearchIcon size={18} />}
                  reason={chatReason(read?.granted, "read")}
                  description={gmail.connection?.account_email ?? "Your mailbox"}
                  onSelect={() => onStarter(STARTERS.search)}
                >
                  Search my email
                </GmailItem>
              ) : null}
              {showDraft ? (
                <GmailItem
                  icon={<PenIcon size={18} />}
                  reason={chatReason(draft?.granted, "draft")}
                  description="Saved in your Gmail drafts for you to review"
                  onSelect={() => onStarter(STARTERS.draft)}
                >
                  Draft an email
                </GmailItem>
              ) : null}
              {showImport ? (
                <GmailItem
                  icon={<UploadIcon size={18} />}
                  reason={read?.granted ? null : "Grant Gmail read access in Settings → Plugins & data."}
                  description="Threads and attachments into My documents"
                  onSelect={onImportGmail}
                >
                  Import from Gmail…
                </GmailItem>
              ) : null}
            </MenuGroup>
          ) : null
        ) : null}
      </Menu>
      {/* Outside the menu panel, so it survives the menu closing; clicked synchronously by "Attach files…". */}
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        tabIndex={-1}
        accept={config?.supported_extensions.join(",")}
        onChange={onFilesPicked}
      />
    </>
  );
}

type GmailItemProps = {
  icon: ReactNode;
  reason: string | null;
  description: string;
  onSelect: () => void;
  children: ReactNode;
};

function GmailItem({ icon, reason, description, onSelect, children }: GmailItemProps) {
  return (
    <MenuItem
      icon={<span className="ui-menu-icon chat-plus-gmail">{icon}</span>}
      description={reason ?? description}
      disabled={reason !== null}
      onSelect={onSelect}
    >
      {children}
    </MenuItem>
  );
}
