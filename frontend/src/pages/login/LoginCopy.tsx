import { BookIcon, LockIcon, SearchIcon } from "../../components/ui/icons";

// The owner's words for the sign-in page (visual spec 6.28), kept verbatim. On desktops the hero picture and the deck
// lead the aside over the product tour and the pitch follows it; on phones the picture sits above the form and the
// deck and the pitch below it.

/** The tagline. The hero picture shows it, so it is a heading for assistive technology only. */
export function Headline() {
  return <h2 className="sr-only">Your documents. Your AI. Your answers.</h2>;
}

export function Deck() {
  return (
    <>
      {/* The line may break after the dash, not before it. */}
      <p className="login-sub">
        Unlock the intelligence hidden in your <span className="login-nobreak">documents—</span>without compromising
        confidentiality.
      </p>
      <p className="login-lead">
        Upload your contracts, internal reports, policies, and confidential handbooks. Ask questions in natural
        language, explore complex information, and get clear, context-aware answers with references to the exact
        passages behind them.
      </p>
    </>
  );
}

/** The second paragraph, its two short sentences set as items, then the closing line. */
export function Pitch() {
  return (
    <div className="login-pitch">
      <ul className="login-pitch-list">
        <li>
          <span className="login-pitch-icon">
            <SearchIcon size={17} />
          </span>
          No endless searching.
        </li>
        <li>
          <span className="login-pitch-icon">
            <BookIcon size={17} />
          </span>
          No reading hundreds of pages.
        </li>
      </ul>
      <p className="login-pitch-text">
        Just a smarter way to work with your knowledge, with privacy and security at the heart of the experience.
      </p>
      <div className="login-closing">
        <span className="login-closing-mark">
          <LockIcon size={15} />
        </span>
        <p>Your knowledge deserves a more private way to AI.</p>
      </div>
    </div>
  );
}
