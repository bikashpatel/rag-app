import { SendIcon, SparkleIcon } from "./icons";

const EXAMPLE_PROMPTS = [
  "Summarize the key points of my documents",
  "Which dates, deadlines or figures are mentioned?",
  "Explain the main topic in simple terms",
];
const FILE_PROMPT = "Create a PDF report summarizing my documents";
const IMAGE_PROMPT = "Draw a watercolor illustration of a lighthouse at dawn";

type EmptyStateProps = {
  username: string | null;
  disabled: boolean;
  /** The selected model can create files: adds an example that asks for one. */
  canCreateFiles: boolean;
  /** The selected model can generate images: adds an example that asks for one. */
  canCreateImages: boolean;
  onPick: (prompt: string) => void;
};

/** Start screen of a new chat with clickable example prompts. */
export default function EmptyState({ username, disabled, canCreateFiles, canCreateImages, onPick }: EmptyStateProps) {
  const prompts = [
    ...EXAMPLE_PROMPTS,
    ...(canCreateFiles ? [FILE_PROMPT] : []),
    ...(canCreateImages ? [IMAGE_PROMPT] : []),
  ];
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
        {prompts.map((prompt) => (
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
