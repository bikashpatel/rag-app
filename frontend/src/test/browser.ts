// Minimal stand-ins for the browser APIs jsdom lacks (or answers without layout) that the app relies on.

type MediaPredicate = (query: string) => boolean;

let mediaPredicate: MediaPredicate = () => false;
const mediaLists = new Set<FakeMediaQueryList>();

class FakeMediaQueryList extends EventTarget {
  readonly media: string;
  onchange: ((event: Event) => void) | null = null;
  private last: boolean;

  constructor(media: string) {
    super();
    this.media = media;
    this.last = mediaPredicate(media);
  }

  get matches(): boolean {
    return mediaPredicate(this.media);
  }

  addListener(listener: (event: Event) => void): void {
    this.addEventListener("change", listener);
  }

  removeListener(listener: (event: Event) => void): void {
    this.removeEventListener("change", listener);
  }

  notify(): void {
    const now = this.matches;
    if (now === this.last) return;
    this.last = now;
    const event = Object.assign(new Event("change"), { matches: now, media: this.media });
    this.onchange?.(event);
    this.dispatchEvent(event);
  }
}

/** Makes `window.matchMedia` answer with `predicate` and fires "change" on every list whose answer changed. */
export function setMediaQueries(predicate: MediaPredicate): void {
  mediaPredicate = predicate;
  mediaLists.forEach((list) => list.notify());
}

export function resetMediaQueries(): void {
  mediaPredicate = () => false;
  mediaLists.clear();
}

class NoopResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const ONE_PIXEL = { x: 0, y: 0, top: 0, left: 0, right: 1, bottom: 1, width: 1, height: 1 };

export function installBrowserStubs(): void {
  window.matchMedia = (query: string) => {
    const list = new FakeMediaQueryList(query);
    mediaLists.add(list);
    return list as unknown as MediaQueryList;
  };
  window.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView = () => {};
  Element.prototype.scrollTo = () => {};
  // jsdom has no layout, so every element reports no boxes; connected elements outside [hidden] get one, so focus
  // management that skips invisible controls (Modal's Tab trap) sees them.
  Element.prototype.getClientRects = function (this: Element) {
    const visible = this.isConnected && !this.closest("[hidden]");
    return (visible ? [ONE_PIXEL] : []) as unknown as DOMRectList;
  };
}
