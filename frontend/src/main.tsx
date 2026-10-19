import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Before App, so the feature stylesheets App pulls in load after the fonts, global tokens and primitives.
import "./styles/fonts.css";
import "./styles/global.css";
import App from "./App";
import { handleExternalPreferences } from "./auth/AuthContext";
import { blockStrayFileDrops } from "./lib/blockStrayFileDrops";
import { startAppearanceEngine } from "./theme/appearance";

// public/theme-boot.js already applied the cached appearance before the first paint; the engine takes over from here
// (System follows the OS live, other tabs' changes arrive through AuthContext).
startAppearanceEngine(handleExternalPreferences);
blockStrayFileDrops();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
