import { useEffect, useState } from "react";

/** What the empty composer types out, one after another; none needs a plan feature. */
export const COMPOSER_EXAMPLES = [
  "Ask a question…",
  "Summarize the key points of my documents",
  "Which dates or deadlines are mentioned?",
  "Explain the main topic in simple terms",
];

export const TYPE_MS = 48;
export const ERASE_MS = 22;
export const HOLD_MS = 1800;
export const GAP_MS = 350;

/**
 * The empty composer's animated placeholder (the owner's choice, 2026-11-04): it shows an example, erases it, types the
 * next and holds it, with a blinking caret. Decorative: the textarea keeps its placeholder for assistive tech, hidden by
 * CSS while this shows; the Composer leaves it out under "reduce motion".
 */
export default function ComposerExamples() {
  const [shown, setShown] = useState(COMPOSER_EXAMPLES[0]);

  useEffect(() => {
    let example = 0;
    let length = COMPOSER_EXAMPLES[0].length;
    let erasing = true;
    let timer = window.setTimeout(step, HOLD_MS);

    function step() {
      const text = COMPOSER_EXAMPLES[example];
      let delay = erasing ? ERASE_MS : TYPE_MS;
      if (erasing) {
        length -= 1;
        if (length === 0) {
          erasing = false;
          example = (example + 1) % COMPOSER_EXAMPLES.length;
          delay = GAP_MS;
        }
      } else {
        length += 1;
        if (length === text.length) {
          erasing = true;
          delay = HOLD_MS;
        }
      }
      setShown(text.slice(0, length));
      timer = window.setTimeout(step, delay);
    }

    return () => window.clearTimeout(timer);
  }, []);

  return (
    <span className="chat-composer-example" aria-hidden="true">
      {shown}
      <span className="chat-composer-example-caret" />
    </span>
  );
}
