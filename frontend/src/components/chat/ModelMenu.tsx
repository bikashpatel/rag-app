import type { ChatModel } from "../../api/types";
import Menu, { MenuGroup, MenuItem, MenuRadioItem, MenuSeparator } from "../ui/Menu";
import { ChevronDownIcon, SlidersIcon } from "../ui/icons";
import type { ModelsState } from "./useModels";

const PROVIDER_NAMES: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", ollama: "Ollama" };

function providerName(provider: string): string {
  return PROVIDER_NAMES[provider] ?? provider.charAt(0).toUpperCase() + provider.slice(1);
}

function groupByProvider(models: ChatModel[]): [string, ChatModel[]][] {
  const groups = new Map<string, ChatModel[]>();
  for (const model of models) groups.set(model.provider, [...(groups.get(model.provider) ?? []), model]);
  return [...groups];
}

type ModelMenuProps = {
  models: ModelsState;
  /** Settings → General (where the default is set). */
  onOpenSettings: () => void;
};

/**
 * The model for this chat (functional brief 8.2, visual spec 6.13): a menu button with the selected model's full
 * name (ellipsized, never shortened), models grouped by provider as radio items with their Files/Images tags, the
 * default marked, and a link to set the default in Settings.
 */
export default function ModelMenu({ models: state, onOpenSettings }: ModelMenuProps) {
  const { models, error, selectedId, defaultId, select } = state;
  const selected = models?.find((model) => model.id === selectedId) ?? null;
  const placeholder = models === null ? (error ? "Default model" : "Loading models…") : "No models available";
  const name = selected?.display_name ?? placeholder;
  const groups = models ? groupByProvider(models) : [];

  const item = (model: ChatModel) => {
    const tags = [model.can_create_files ? "Files" : null, model.can_create_images ? "Images" : null].filter(
      (tag): tag is string => tag !== null,
    );
    return (
      <MenuRadioItem
        key={model.id}
        className="chat-model-item"
        checked={model.id === selectedId}
        label={model.id === defaultId ? `${model.display_name} (default)` : undefined}
        onSelect={() => select(model.id)}
        description={
          tags.length > 0 ? (
            <span className="chat-model-tags">
              <span className="sr-only">Can create </span>
              {tags.map((tag, i) => (
                <span key={tag} className="chat-model-tag">
                  {tag}
                  {i < tags.length - 1 ? <span className="sr-only"> and </span> : null}
                </span>
              ))}
            </span>
          ) : undefined
        }
      >
        <span className="chat-model-name">{model.display_name}</span>
        {model.id === defaultId ? <span className="badge badge-accent chat-model-default">Default</span> : null}
      </MenuRadioItem>
    );
  };

  return (
    <Menu
      label="Model"
      placement="bottom-start"
      className="chat-model-menu"
      trigger={(props) => (
        <button {...props} type="button" className="chat-model-btn" aria-label={`Model: ${name}`} title={name}>
          <span className="chat-model-btn-label">{name}</span>
          <ChevronDownIcon size={15} />
        </button>
      )}
    >
      {models === null ? (
        <MenuItem disabled description={error ?? undefined}>
          {error ? "Default model" : "Loading models…"}
        </MenuItem>
      ) : models.length === 0 ? (
        <MenuItem disabled description="Ask your administrator to enable a chat model.">
          No models available
        </MenuItem>
      ) : groups.length > 1 ? (
        groups.map(([provider, items]) => (
          <MenuGroup key={provider} label={providerName(provider)}>
            {items.map(item)}
          </MenuGroup>
        ))
      ) : (
        <MenuGroup ariaLabel="Models">{models.map(item)}</MenuGroup>
      )}
      <MenuSeparator />
      <MenuItem icon={<SlidersIcon size={18} />} onSelect={onOpenSettings}>
        Set the default in Settings…
      </MenuItem>
    </Menu>
  );
}
