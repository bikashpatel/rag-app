import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";
import { publishDocuments } from "../components/documents/documentEvents";
import { announceUserChanged } from "../lib/appEvents";
import { installBrowserStubs, resetMediaQueries } from "./browser";

installBrowserStubs();
// The default 1 s wait is too short on a loaded machine (parallel suites, CI); a passing wait returns as soon as it can.
configure({ asyncUtilTimeout: 5_000 });

afterEach(() => {
  cleanup();
  // The per-user module stores (plugins, uploads, the documents snapshot) outlive a test: reset them explicitly.
  announceUserChanged(null);
  publishDocuments(null);
  resetMediaQueries();
  localStorage.clear();
  sessionStorage.clear();
});
