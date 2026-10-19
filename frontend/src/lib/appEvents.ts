// App-wide events between areas that don't share props (module stores subscribe at load, components in effects).

function channel<T extends unknown[]>() {
  const listeners = new Set<(...args: T) => void>();
  return {
    emit: (...args: T) => [...listeners].forEach((listener) => listener(...args)),
    subscribe: (listener: (...args: T) => void): (() => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

const conversationsDeleted = channel<[]>();
const userChanged = channel<[userId: number | null]>();

/** Every conversation of the user was deleted (Settings → Plugins & data → Delete all chats). */
export function announceConversationsDeleted(): void {
  conversationsDeleted.emit();
}

export function onConversationsDeleted(listener: () => void): () => void {
  return conversationsDeleted.subscribe(listener);
}

/** Called by AuthContext when the signed-in user's id changes (null = signed out): per-user state must be cleared. */
export function announceUserChanged(userId: number | null): void {
  userChanged.emit(userId);
}

export function onUserChanged(listener: (userId: number | null) => void): () => void {
  return userChanged.subscribe(listener);
}
