import { SendIcon, SparkleIcon } from "./icons";

const EXAMPLE_PROMPTS = [
  "Summarize the key points of my documents",
  "Which dates, deadlines or figures are mentioned?",
  "Explain the main topic in simple terms",
];

type EmptyStateProps = {
  username: string | null;
  disabled: boolean;
  onPick: (prompt: string) => void;
};

/** Start screen of a new chat with clickable example prompts. */
export default function EmptyState({ username, disabled, onPick }: EmptyStateProps) {
  return (
    <div className="chat-empty">
      <div className="chat-empty-logo" aria-hidden="true">
        <SparkleIcon size={22} />
      </div>
      <h2 className="chat-empty-title">{username ? `How can I help, ${username}?` : "How can I help?"}</h2>
      <p className="chat-empty-text">
        Ask about your documents or anything else. Answers cite the passages they draw on, so you can check them.
      </p>
      <ul className="chat-examples" aria-label="Example questions">
        {EXAMPLE_PROMPTS.map((prompt) => (
          <li key={prompt}>
            <button type="button" className="chat-example" onClick={() => onPick(prompt)} disabled={disabled}>
              <span>{prompt}</span>
              <SendIcon size={14} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
