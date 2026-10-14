function carriesFiles(event: DragEvent): boolean {
  return event.dataTransfer ? Array.from(event.dataTransfer.types).includes("Files") : false;
}

/**
 * Stop the browser from opening a file dropped outside a drop zone. Its default is to load the file in the tab
 * (Safari, Firefox) or a new tab (Chromium), which would unload the app and abort in-flight uploads and the
 * streaming answer. Only file drags are touched, so text and link drags behave normally.
 *
 * These listeners run in the window's bubble phase, after React's root listener, so a real drop zone
 * (the documents panel via useFileDrop) has already called preventDefault and chosen its dropEffect: skip those
 * events instead of overwriting "copy" with "none", which would break dropping onto the panel.
 */
export function blockStrayFileDrops(): void {
  window.addEventListener("dragover", (event) => {
    if (!carriesFiles(event) || event.defaultPrevented) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "none"; // "not allowed" cursor outside the panel
  });
  window.addEventListener("drop", (event) => {
    if (carriesFiles(event)) event.preventDefault();
  });
}
