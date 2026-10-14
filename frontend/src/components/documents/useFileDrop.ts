import { useRef, useState, type DragEvent } from "react";

function carriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}

/** Split a drop into files and folder names (folders can't be uploaded and are reported by name). */
function readDrop(data: DataTransfer): { files: File[]; folders: string[] } {
  const files: File[] = [];
  const folders: string[] = [];
  for (const item of Array.from(data.items)) {
    if (item.kind !== "file") continue;
    const entry = item.webkitGetAsEntry();
    if (entry?.isDirectory) {
      folders.push(entry.name);
      continue;
    }
    const file = item.getAsFile();
    if (file) files.push(file);
  }
  return { files, folders };
}

/**
 * Drag-and-drop handlers for a whole region. Only file drags are handled, so text and link drags behave normally;
 * file drops on the region are always intercepted, even while uploads are disabled. Drops anywhere else are
 * blocked by the app-wide guard in lib/blockStrayFileDrops, so the browser never navigates away to the file.
 */
export function useFileDrop(onDrop: (files: File[], folders: string[]) => void, enabled: boolean) {
  const [dragActive, setDragActive] = useState(false);
  const depth = useRef(0);

  const dropHandlers = {
    onDragEnter(event: DragEvent<HTMLElement>) {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth.current += 1;
      setDragActive(enabled);
    },
    onDragOver(event: DragEvent<HTMLElement>) {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = enabled ? "copy" : "none";
    },
    onDragLeave(event: DragEvent<HTMLElement>) {
      if (!carriesFiles(event)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragActive(false);
    },
    onDrop(event: DragEvent<HTMLElement>) {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth.current = 0;
      setDragActive(false);
      if (!enabled) return;
      const { files, folders } = readDrop(event.dataTransfer);
      if (files.length > 0 || folders.length > 0) onDrop(files, folders);
    },
  };

  return { dragActive, dropHandlers };
}
