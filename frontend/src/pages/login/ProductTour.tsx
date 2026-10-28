import { useEffect, useId, useRef, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import { useMediaQuery } from "../../components/chat/useMediaQuery";
import { PauseIcon, PlayIcon } from "../../components/ui/icons";
import { TOUR_SCENES } from "./tourScenes";
import "./product-tour.css";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
const LAST = TOUR_SCENES.length - 1;

/** The scene on show, a count of scene changes, and the change that last showed each scene (its miniature's key). */
type Position = { index: number; visit: number; starts: number[] };

const FIRST: Position = { index: 0, visit: 0, starts: TOUR_SCENES.map(() => 0) };

function moveTo(position: Position, index: number): Position {
  const visit = position.visit + 1;
  return { index, visit, starts: position.starts.map((start, i) => (i === index ? visit : start)) };
}

function subscribeVisibility(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

const documentHidden = () => document.visibilityState === "hidden";

/**
 * The sign-in aside's product tour: a carousel with tabs (WAI-ARIA APG) that plays one short scene at a time. It
 * rotates on its own (WCAG 2.2.2: a Pause button), holds while the pointer or focus is inside it or the page is hidden,
 * and under reduced motion shows finished scenes that change only from the tabs. One timeout runs at a time.
 */
export default function ProductTour() {
  const id = useId();
  const reduced = useMediaQuery(REDUCED_MOTION);
  const hidden = useSyncExternalStore(subscribeVisibility, documentHidden);
  const [{ index, visit, starts }, setPosition] = useState(FIRST);
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  // A scene shown finished while paused stays finished when Play resumes the tour, rather than replaying.
  const [settledVisit, setSettledVisit] = useState(-1);
  const clock = useRef({ visit: -1, left: 0 });
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);

  const rotating = !paused && !reduced;
  const held = hovered || focused;
  const running = rotating && !held && !hidden;
  const still = !rotating || settledVisit === visit;
  const duration = TOUR_SCENES[index].duration;

  useEffect(() => {
    if (!running) return;
    if (clock.current.visit !== visit) clock.current = { visit, left: duration };
    const started = Date.now();
    const timer = window.setTimeout(
      () => setPosition((position) => moveTo(position, position.index === LAST ? 0 : position.index + 1)),
      clock.current.left,
    );
    return () => {
      window.clearTimeout(timer);
      clock.current.left -= Date.now() - started;
    };
  }, [running, visit, duration]);

  function show(next: number) {
    if (next !== index) setPosition((position) => moveTo(position, next));
  }

  function togglePlay() {
    if (paused) {
      // Play means play now, even with the pointer on the button and focus in it.
      setSettledVisit(visit);
      setHovered(false);
      setFocused(false);
    }
    setPaused(!paused);
  }

  function onTabKeyDown(event: KeyboardEvent) {
    const keys: Record<string, number> = {
      ArrowRight: index === LAST ? 0 : index + 1,
      ArrowLeft: index === 0 ? LAST : index - 1,
      Home: 0,
      End: LAST,
    };
    const next = keys[event.key];
    if (next === undefined) return;
    event.preventDefault();
    show(next);
    tabs.current[next]?.focus();
  }

  return (
    <section
      className="login-tour"
      aria-roledescription="carousel"
      aria-label="Product tour"
      data-running={running || undefined}
      data-reduced={reduced || undefined}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false);
      }}
    >
      <div className="login-tour-controls">
        {reduced ? null : (
          <button
            type="button"
            className="btn btn-ghost btn-icon btn-xs login-tour-toggle"
            aria-label={paused ? "Play the tour" : "Pause the tour"}
            onClick={togglePlay}
          >
            {paused ? <PlayIcon size={14} /> : <PauseIcon size={14} />}
          </button>
        )}
        <div className="login-tour-tabs" role="tablist" aria-label="Tour scenes" onKeyDown={onTabKeyDown}>
          {TOUR_SCENES.map((scene, i) => (
            <button
              key={scene.title}
              ref={(tab) => {
                tabs.current[i] = tab;
              }}
              type="button"
              role="tab"
              id={`${id}-tab-${i}`}
              className="login-tour-tab"
              aria-label={scene.title}
              aria-selected={i === index}
              aria-controls={`${id}-slide-${i}`}
              tabIndex={i === index ? 0 : -1}
              data-seen={i < index || undefined}
              onClick={() => show(i)}
            >
              <span className="login-tour-track">
                <span
                  key={i === index ? visit : "idle"}
                  className="login-tour-fill"
                  style={i === index ? { animationDuration: `${scene.duration}ms` } : undefined}
                />
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="login-tour-slides" aria-live={rotating && !held ? "off" : "polite"}>
        <div className="login-tour-frame" aria-hidden="true">
          <div className="login-tour-sheet" />
          <div className="login-tour-window">
            <span className="login-tour-window-bar">
              <i />
              <i />
              <i />
            </span>
          </div>
        </div>
        {TOUR_SCENES.map(({ title, caption, plan, Scene }, i) => {
          const active = i === index;
          const play = active && !still;
          return (
            <div
              key={title}
              id={`${id}-slide-${i}`}
              className="login-tour-slide"
              role="group"
              aria-roledescription="slide"
              aria-label={`${i + 1} of ${TOUR_SCENES.length}: ${title}`}
              aria-hidden={!active || undefined}
              inert={!active}
              data-active={active || undefined}
            >
              {/* A new key each time the scene comes back restarts its animation; leaving keeps it, to fade out. */}
              <div
                key={starts[i]}
                className={play ? "login-tour-scene login-tour-play" : "login-tour-scene"}
                aria-hidden="true"
              >
                <Scene play={play} />
              </div>
              <div className="login-tour-text">
                <div className="login-tour-head">
                  <h3 className="login-tour-title">{title}</h3>
                  {plan ? <span className="login-tour-plan">{plan}</span> : null}
                </div>
                <p className="login-tour-caption">{caption}</p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
