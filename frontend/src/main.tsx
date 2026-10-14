import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Before App, so the feature stylesheets App pulls in load after the global tokens and primitives.
import "./styles/global.css";
import App from "./App";
import { blockStrayFileDrops } from "./lib/blockStrayFileDrops";

blockStrayFileDrops();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
