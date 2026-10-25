# Frontend tests

Unit and component tests with Vitest, Testing Library and jsdom. Each test sits next to its code as
`src/**/*.test.ts(x)`; this folder holds the shared helpers. No backend, network or browser is needed.

## Run

```bash
cd frontend
npm ci
npx tsc --noEmit            # also type-checks the tests (the Docker build's `npm run build` does too)
npm test                    # vitest run: the whole suite once
npm run test:watch          # re-runs on change
npm run test:coverage       # with v8 coverage (text summary, coverage/index.html); fails below the thresholds
npx vitest run src/components/chat   # a folder or file
```

## Settings (`vite.config.ts`, `test` block)

- **Pinned time zone and locale**: `TZ=UTC` and `LANG`/`LC_ALL=en_US.UTF-8`. The app formats dates and numbers with
  the runtime's zone and locale, so the pins make the output the same on every machine. Keep both.
- **Timeouts** for a loaded machine (parallel suites, CI): 15 s per test, and 5 s for `findBy…`/`waitFor`
  (`setup.ts`). A passing wait returns as soon as it can.
- **Coverage thresholds** just under the current numbers. Raise them when coverage goes up.
- Mocks are restored and stubbed globals removed after each test; `setup.ts` also resets the per-user module stores
  (plugins, uploads, the documents snapshot), storage and media queries.

## Helpers

| File | What |
|---|---|
| `http.ts` | `stubFetch({ "GET /path": body \| (call) => reply })` replaces `fetch` and returns the requests sent (an unrouted request answers 500 naming it); `jsonResponse`, `errorResponse`, `streamResponse`, `deferred` |
| `render.tsx` | `renderWithRouter` and `renderWithAuth` (the real `AuthProvider` after its session check: stub `GET /auth/me`), with `location()` for redirect checks |
| `chatStream.ts` | SSE events as the backend writes them and `liveStream()`, a chat response the test feeds event by event |
| `fixtures.ts`, `adminTabs.ts` | `make…` builders typed with `src/api/types.ts` |
| `documents.ts` | `controlUploads` (a `vi.mock`ed upload the test settles), `settle` and `makeFile` |
| `browser.ts` | what jsdom lacks (`matchMedia`, `ResizeObserver`, scrolling, one-pixel client rects) and `setMediaQueries` for the phone and tablet layouts |

A test that depends on the date pins the clock with `vi.useFakeTimers({ toFake: ["Date"] })` and
`vi.setSystemTime(…)`, and builds its data from fixed ISO strings.
