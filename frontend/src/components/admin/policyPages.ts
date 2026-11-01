import { useCallback, useEffect, useState } from "react";
import { ApiError, publicApi } from "../../api/client";
import type { PageSlug } from "../../api/types";
import { DRAFT_MARKER } from "../../lib/billing";

type PolicyPage = { slug: PageSlug; title: string };

/** The public pages, as their Markdown files name them (frontend/public/legal/<slug>.md). */
export const POLICY_PAGES: readonly PolicyPage[] = [
  { slug: "terms", title: "Terms of service" },
  { slug: "privacy", title: "Privacy policy" },
  { slug: "refunds", title: "Cancellation and refunds" },
  { slug: "shipping", title: "Shipping and delivery" },
  { slug: "contact", title: "Contact us" },
  { slug: "about", title: "About us" },
  { slug: "pricing", title: "Pricing" },
];

/** The pages a visitor accepts when signing up. */
export const SIGNUP_POLICY_PAGES: readonly PolicyPage[] = [
  { slug: "privacy", title: "Privacy policy" },
  { slug: "terms", title: "Terms of service" },
];

export type PageCheck = PolicyPage & { state: "final" | "draft" | "missing" | "error" };

/** Reads each page's Markdown for the draft marker (the going-live checks). `pages` is a module constant. */
export function usePolicyPages(pages: readonly PolicyPage[]): { pages: PageCheck[] | null; reload: () => void } {
  const [checked, setChecked] = useState<PageCheck[] | null>(null);
  const [round, setRound] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setChecked(null);
    void Promise.all(
      pages.map(({ slug, title }) =>
        publicApi.page(slug, controller.signal).then(
          (text): PageCheck => ({ slug, title, state: text.trimStart().startsWith(DRAFT_MARKER) ? "draft" : "final" }),
          (err: unknown): PageCheck => ({
            slug,
            title,
            state: err instanceof ApiError && err.status === 404 ? "missing" : "error",
          }),
        ),
      ),
    ).then((result) => {
      if (!controller.signal.aborted) setChecked(result);
    });
    return () => controller.abort();
  }, [pages, round]);
  return { pages: checked, reload: useCallback(() => setRound((n) => n + 1), []) };
}
