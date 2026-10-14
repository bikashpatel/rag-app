import { useId } from "react";
import type { ChatModel } from "../../api/types";
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

export default function ModelSelect({ models, error, selectedId, select }: ModelsState) {
  const id = useId();
  const groups = models ? groupByProvider(models) : [];
  const placeholder = models === null ? (error ? "Default model" : "Loading models…") : "No models available";

  return (
    <div className="chat-model">
      <label htmlFor={id} className="sr-only">
        Model
      </label>
      {models && models.length > 0 ? (
        <select
          id={id}
          className="select chat-model-select"
          value={selectedId ?? ""}
          onChange={(e) => select(Number(e.target.value))}
        >
          {groups.length > 1
            ? groups.map(([provider, items]) => (
                <optgroup key={provider} label={providerName(provider)}>
                  {items.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.display_name}
                    </option>
                  ))}
                </optgroup>
              ))
            : models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.display_name}
                </option>
              ))}
        </select>
      ) : (
        <select id={id} className="select chat-model-select" disabled title={error ?? undefined}>
          <option>{placeholder}</option>
        </select>
      )}
    </div>
  );
}
