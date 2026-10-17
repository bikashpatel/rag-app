import type { ThreadPluginCard } from "../useChatStream";
import GmailActivityChip from "./GmailActivityChip";
import GmailDraftCard from "./GmailDraftCard";
import GmailImportCard from "./GmailImportCard";
import GmailSendCard from "./GmailSendCard";
import "./plugins.css";

const KNOWN_TYPES: ReadonlySet<string> = new Set(["gmail_activity", "gmail_draft", "gmail_send", "gmail_import"]);

/** Cards of types this client doesn't know (a later plugin's) are left out (SPEC). */
export function isKnownPluginCard(card: ThreadPluginCard): boolean {
  return KNOWN_TYPES.has(card.type);
}

/** A card a plugin added to an answer; types this client doesn't know render nothing. */
export default function PluginCard({ card }: { card: ThreadPluginCard }) {
  const { stopped } = card;
  switch (card.type) {
    case "gmail_activity":
      return <GmailActivityChip card={card} stopped={stopped} />;
    case "gmail_draft":
      return <GmailDraftCard card={card} stopped={stopped} />;
    case "gmail_send":
      return <GmailSendCard card={card} stopped={stopped} />;
    case "gmail_import":
      return <GmailImportCard card={card} stopped={stopped} />;
    default:
      return null;
  }
}
