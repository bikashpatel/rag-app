import { useCallback, useEffect, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, publicApi } from "../../api/client";
import type { PageSlug } from "../../api/types";
import Spinner from "../../components/ui/Spinner";
import { AlertCircleIcon, RefreshIcon } from "../../components/ui/icons";
import { DRAFT_MARKER } from "../../lib/billing";
import { contactLine, usePublicSite } from "./PublicLayout";

const remarkPlugins = [remarkGfm];
/** The page's own title is the file's first "# " heading; any other level-1 heading reads as a section. */
const components: Components = { h1: ({ children }) => <h2>{children}</h2> };

/** The title when a file has no "# " heading. */
export const PAGE_TITLES: Record<PageSlug, string> = {
  pricing: "Pricing",
  terms: "Terms of service",
  privacy: "Privacy policy",
  refunds: "Cancellation and refunds",
  contact: "Contact us",
  about: "About us",
  shipping: "Shipping and delivery",
};

export type PolicyDocument = { draft: boolean; title: string | null; updated: string | null; body: string };

/** A policy page's Markdown: the draft marker (first line), the "# " title, a leading "Last updated …" line, the rest. */
export function parsePolicy(text: string): PolicyDocument {
  const lines = text.replace(/^﻿/, "").replace(/\r\n?/g, "\n").split("\n");
  const draft = lines[0]?.trim() === DRAFT_MARKER;
  if (draft) lines.shift();
  let title: string | null = null;
  const titleAt = lines.findIndex((line) => /^#\s+\S/.test(line));
  if (titleAt !== -1) {
    title = lines[titleAt].replace(/^#\s+/, "").trim();
    lines.splice(titleAt, 1);
  }
  let updated: string | null = null;
  const firstText = lines.findIndex((line) => line.trim() !== "");
  if (firstText !== -1 && /^[*_]*last updated\b/i.test(lines[firstText].trim())) {
    updated = lines[firstText].trim().replace(/^[*_]+|[*_]+$/g, "");
    lines.splice(firstText, 1);
  }
  return { draft, title, updated, body: lines.join("\n").trim() };
}

type DocumentState =
  | { status: "loading" }
  | { status: "ready"; doc: PolicyDocument }
  | { status: "missing" }
  | { status: "error"; message: string };

/** Loads and parses /legal/<slug>.md; `retry` loads it again. */
export function usePolicyDocument(slug: PageSlug): { state: DocumentState; retry: () => void } {
  const [state, setState] = useState<DocumentState>({ status: "loading" });
  const [round, setRound] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    publicApi.page(slug, controller.signal).then(
      (text) => setState({ status: "ready", doc: parsePolicy(text) }),
      (err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) setState({ status: "missing" });
        else setState({ status: "error", message: err instanceof ApiError ? err.message : "Couldn't load this page." });
      },
    );
    return () => controller.abort();
  }, [slug, round]);
  return { state, retry: useCallback(() => setRound((n) => n + 1), []) };
}

/** Sets the tab title while the page is shown. */
export function usePageTitle(title: string, businessName: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = `${title} · ${businessName}`;
    return () => {
      document.title = previous;
    };
  }, [title, businessName]);
}

export function DraftBand() {
  return (
    <div className="public-draft" role="note">
      <span className="badge badge-warning">Draft</span>
      <span>This page is a placeholder. The owner hasn't published its final text yet.</span>
    </div>
  );
}

export function PolicyProse({ markdown }: { markdown: string }) {
  return (
    <div className="public-prose">
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

export function PageLoading() {
  return (
    <div className="public-state">
      <Spinner size={20} label="Loading the page" />
    </div>
  );
}

export function PageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="public-state" role="alert">
      <p>
        <AlertCircleIcon size={16} />
        {message}
      </p>
      <button type="button" className="btn btn-sm" onClick={onRetry}>
        <RefreshIcon size={15} />
        Try again
      </button>
    </div>
  );
}

/** /terms, /privacy, /refunds, /contact, /about, /shipping: the Markdown of frontend/public/legal/<slug>.md. */
export default function PolicyPage({ slug }: { slug: Exclude<PageSlug, "pricing"> }) {
  const site = usePublicSite();
  const { state, retry } = usePolicyDocument(slug);
  const doc = state.status === "ready" ? state.doc : null;
  const title = doc?.title ?? PAGE_TITLES[slug];
  usePageTitle(title, site.business_name);
  const contact = contactLine(site);

  return (
    <>
      {doc?.draft ? <DraftBand /> : null}
      <main className="public-main" id="main">
        <h1 className="public-title">{title}</h1>
        {doc?.updated ? <p className="public-updated">{doc.updated}</p> : null}
        {state.status === "loading" ? <PageLoading /> : null}
        {state.status === "missing" ? <p className="public-state">This page hasn't been written yet.</p> : null}
        {state.status === "error" ? <PageError message={state.message} onRetry={retry} /> : null}
        {doc ? <PolicyProse markdown={doc.body} /> : null}
        {slug === "contact" && (contact || site.address) ? (
          <dl className="public-facts" aria-label="Contact details">
            {site.contact_email ? (
              <div>
                <dt>Email</dt>
                <dd>
                  <a href={`mailto:${site.contact_email}`}>{site.contact_email}</a>
                </dd>
              </div>
            ) : null}
            {site.contact_phone ? (
              <div>
                <dt>Phone</dt>
                <dd>
                  <a href={`tel:${site.contact_phone.replace(/[^\d+]/g, "")}`}>{site.contact_phone}</a>
                </dd>
              </div>
            ) : null}
            {site.address ? (
              <div>
                <dt>Address</dt>
                <dd className="public-address">{site.address}</dd>
              </div>
            ) : null}
          </dl>
        ) : null}
      </main>
    </>
  );
}
