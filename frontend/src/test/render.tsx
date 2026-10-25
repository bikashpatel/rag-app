import { render, waitFor, type RenderResult } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import { MemoryRouter, useLocation, type Location } from "react-router-dom";
import { AuthProvider, useAuth } from "../auth/AuthContext";

type Options = {
  /** The initial URL (or a router entry with state). */
  route?: string | { pathname: string; search?: string; state?: unknown };
};

type Rendered = RenderResult & {
  /** The router's current location (path, search and hash), for redirect assertions. */
  location: () => string;
  locationState: () => unknown;
};

function LocationProbe({ onChange }: { onChange: (location: Location) => void }) {
  const location = useLocation();
  useEffect(() => {
    onChange(location);
  }, [location, onChange]);
  return null;
}

function AuthReady({ children, onReady }: { children: ReactNode; onReady: () => void }) {
  const { loading } = useAuth();
  useEffect(() => {
    if (!loading) onReady();
  }, [loading, onReady]);
  return loading ? null : <>{children}</>;
}

function withRouter(ui: ReactNode, { route = "/" }: Options): { tree: ReactNode; current: { location: Location | null } } {
  const current: { location: Location | null } = { location: null };
  const tree = (
    <MemoryRouter initialEntries={[route]}>
      <LocationProbe onChange={(location) => (current.location = location)} />
      {ui}
    </MemoryRouter>
  );
  return { tree, current };
}

function rendered(result: RenderResult, current: { location: Location | null }): Rendered {
  return {
    ...result,
    location: () => {
      const { pathname = "", search = "", hash = "" } = current.location ?? {};
      return pathname + search + hash;
    },
    locationState: () => current.location?.state,
  };
}

/** Renders inside a MemoryRouter. */
export function renderWithRouter(ui: ReactNode, options: Options = {}): Rendered {
  const { tree, current } = withRouter(ui, options);
  return rendered(render(tree), current);
}

/**
 * Renders inside a MemoryRouter and the real AuthProvider, once its session check (GET /auth/me, which the test stubs)
 * has finished. Stub `/auth/me` with a Me to render signed in.
 */
export async function renderWithAuth(ui: ReactNode, options: Options = {}): Promise<Rendered> {
  let ready = false;
  const { tree, current } = withRouter(
    <AuthProvider>
      <AuthReady onReady={() => (ready = true)}>{ui}</AuthReady>
    </AuthProvider>,
    options,
  );
  const result = render(tree);
  await waitFor(() => {
    if (!ready) throw new Error("The session check hasn't finished");
  });
  return rendered(result, current);
}
