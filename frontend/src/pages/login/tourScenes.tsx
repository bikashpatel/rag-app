import { Fragment, type CSSProperties, type ReactNode } from "react";
import FileTag from "../../components/documents/FileTag";
import {
  ArrowUpIcon,
  CheckIcon,
  DownloadIcon,
  FileIcon,
  LockIcon,
  MailIcon,
  SearchIcon,
  SparkleIcon,
  UploadIcon,
  UsersIcon,
} from "../../components/ui/icons";

// The six scenes of the sign-in page's product tour (ProductTour.tsx). Each is a decorative miniature of the app.
// Times are seconds from the scene's start: the CSS reads them as --d (when a part arrives) and --to (when a passing
// state ends), and animates only under .login-tour-play. Without it the markup is the finished scene, and parts that
// only exist mid-animation (typing, "Queued", "Indexing…", placeholders) aren't rendered (`play` is false).

type SceneProps = { play: boolean };

export type TourScene = {
  title: string;
  caption: string;
  /** The lowest plan with the feature (docs/briefs/plans-billing.md), shown as a quiet tag. */
  plan?: string;
  /** Milliseconds on screen while the tour rotates: the animation, then a hold. */
  duration: number;
  Scene: (props: SceneProps) => ReactNode;
};

const TYPE_STEP = 0.03;
const WORD_STEP = 0.065;

function at(start: number, end?: number): CSSProperties {
  const times: Record<string, string> = { "--d": `${start}s` };
  if (end !== undefined) times["--to"] = `${end}s`;
  return times as CSSProperties;
}

/** The composer: the question types itself from `start` and leaves at `send`; at rest it shows the placeholder. */
function Composer({ play, question, start, send }: SceneProps & { question: string; start: number; send: number }) {
  const chars = [...question];
  const last = chars.length - 1;
  return (
    <div className="login-tour-composer">
      <span className="login-tour-composer-text">
        <span className="login-tour-placeholder login-tour-gap" style={at(start, send + 0.25)}>
          Ask about your documents
        </span>
        {play ? (
          <span className="login-tour-typed login-tour-out" style={at(send)}>
            {chars.map((char, i) => (
              <span
                key={i}
                className="login-tour-char"
                style={at(start + i * TYPE_STEP, i === last ? send : start + (i + 1) * TYPE_STEP)}
              >
                {char}
              </span>
            ))}
          </span>
        ) : null}
      </span>
      <span className="login-tour-send login-tour-press" style={at(send)}>
        <ArrowUpIcon size={14} />
      </span>
    </div>
  );
}

function Bubble({ text, at: time }: { text: string; at: number }) {
  return (
    <p className="login-tour-bubble login-tour-in" style={at(time)}>
      {text}
    </p>
  );
}

/** The assistant's turn: the mark arrives at `from`; typing dots show until `until` (when given). */
function Reply({
  play,
  from,
  until,
  fill = false,
  children,
}: SceneProps & { from: number; until?: number; fill?: boolean; children: ReactNode }) {
  return (
    <div className={fill ? "login-tour-reply login-tour-reply-fill" : "login-tour-reply"}>
      <span className="login-tour-mark login-tour-in" style={at(from)}>
        <SparkleIcon size={12} />
      </span>
      <div className="login-tour-reply-body">
        {play && until !== undefined ? (
          <span className="login-tour-dots login-tour-only" style={at(from, until)}>
            <i />
            <i />
            <i />
          </span>
        ) : null}
        {children}
      </div>
    </div>
  );
}

type Words = (string | { strong: string })[];

/** An answer streaming in word by word from `start`, the citation glued to its last word. */
function Answer({ parts, start, cite }: { parts: Words; start: number; cite?: string }) {
  const words = parts.flatMap((part) =>
    typeof part === "string"
      ? part.split(" ").map((text) => ({ text, strong: false }))
      : part.strong.split(" ").map((text) => ({ text, strong: true })),
  );
  const last = words.length - 1;
  return (
    <p className="login-tour-answer">
      {words.map((word, i) => {
        const node = (
          <span className="login-tour-word" style={at(start + i * WORD_STEP)}>
            {word.strong ? <strong>{word.text}</strong> : word.text}
          </span>
        );
        return (
          <Fragment key={i}>
            {i > 0 ? " " : null}
            {i === last && cite ? (
              <span className="login-tour-glue">
                {node}
                <span className="login-tour-cite login-tour-pop" style={at(start + last * WORD_STEP + 0.12)}>
                  {cite}
                </span>
              </span>
            ) : (
              node
            )}
          </Fragment>
        );
      })}
    </p>
  );
}

/** A source pill under an answer: a file or mail dot, the name, then a muted detail. */
function Source({
  mail = false,
  name,
  detail,
  at: time,
}: {
  mail?: boolean;
  name: string;
  detail: string;
  at: number;
}) {
  return (
    <span className="login-tour-source login-tour-in" style={at(time)}>
      <span className="login-tour-source-dot">{mail ? <MailIcon size={12} /> : <FileIcon size={12} />}</span>
      <span className="login-tour-source-name">{name}</span>
      <span className="login-tour-muted">{detail}</span>
    </span>
  );
}

function ReadyMark({ at: time }: { at?: number }) {
  const arrives = time !== undefined;
  return (
    <span
      className={arrives ? "login-tour-ready login-tour-in" : "login-tour-ready"}
      style={arrives ? at(time) : undefined}
    >
      <CheckIcon size={13} />
      Ready
    </span>
  );
}

// ---------- 1. Cited answers ----------

const ASK = "When does the Northwind contract renew?";

function AskScene({ play }: SceneProps) {
  const send = 1.85;
  return (
    <div className="login-tour-chat login-tour-ask">
      <div className="login-tour-thread">
        <Bubble text={ASK} at={send + 0.05} />
        <Reply play={play} from={2.05} until={2.45}>
          <Answer
            start={2.45}
            cite="1"
            parts={[
              "It renews every 12 months on 1 March, unless either side gives",
              { strong: "60 days’ written notice." },
            ]}
          />
          <Source name="Northwind-MSA-2025.pdf" detail="p. 12" at={3.65} />
        </Reply>
      </div>
      <Composer play={play} question={ASK} start={0.45} send={send} />
    </div>
  );
}

// ---------- 2. Uploads ----------

const UPLOADS = [
  { name: "Lease-2019-scan.pdf", meta: "Scanned, 14 pages" },
  { name: "Onboarding-handbook.docx", meta: "38 pages" },
  { name: "Q3-sales.xlsx", meta: "4 sheets" },
  { name: "Board-update.pptx", meta: "22 slides" },
];

function FilesScene({ play }: SceneProps) {
  return (
    <div className="login-tour-files">
      <div className="login-tour-dropzone login-tour-drag" style={at(0.1, 1.45)}>
        <span className="login-tour-dropzone-icon">
          <UploadIcon size={16} />
        </span>
        <span>
          Drop files here or <u>browse</u>
        </span>
      </div>
      <ul className="login-tour-file-list">
        {UPLOADS.map((file, i) => {
          const land = 0.3 + i * 0.28;
          const indexing = land + 0.5;
          const ready = 1.95 + i * 0.42;
          return (
            <li key={file.name} className="login-tour-file login-tour-land" style={at(land)}>
              <FileTag filename={file.name} />
              <span className="login-tour-file-name">
                <b>{file.name}</b>
                <small>{file.meta}</small>
              </span>
              <span className="login-tour-status">
                {play ? (
                  <>
                    <span className="badge login-tour-only" style={at(land, indexing)}>
                      Queued
                    </span>
                    <span className="badge badge-info login-tour-only" style={at(indexing, ready)}>
                      <span className="spinner" />
                      Indexing…
                    </span>
                  </>
                ) : null}
                <ReadyMark at={ready} />
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------- 3. Files from chat ----------

const REPORT = "Make a PDF report of Q3 sales";

function ReportScene({ play }: SceneProps) {
  const send = 1.55;
  return (
    <div className="login-tour-chat">
      <div className="login-tour-thread">
        <Bubble text={REPORT} at={send + 0.05} />
        <Reply play={play} from={1.75} until={2.1}>
          <Answer start={2.1} parts={["Here it is, saved to your documents."]} />
          <div className="login-tour-card login-tour-file-card login-tour-in" style={at(2.65)}>
            <FileTag filename="Q3-sales-report.pdf" />
            <span className="login-tour-file-name">
              <b>Q3-sales-report.pdf</b>
              <span className="login-tour-file-meta">
                {play ? (
                  <span className="login-tour-writing login-tour-only" style={at(2.65, 3.85)}>
                    Writing the file…
                    <span className="login-tour-meter">
                      <span className="login-tour-meter-fill" style={at(2.75)} />
                    </span>
                  </span>
                ) : null}
                <small className="login-tour-in" style={at(3.85)}>
                  <span>4 pages</span>
                  <ReadyMark />
                </small>
              </span>
            </span>
            <span className="btn btn-xs login-tour-download login-tour-pop" style={at(4)}>
              <DownloadIcon size={13} />
              <span className="login-tour-download-label">Download</span>
            </span>
          </div>
        </Reply>
      </div>
      <Composer play={play} question={REPORT} start={0.45} send={send} />
    </div>
  );
}

// ---------- 4. Images ----------

const PICTURE = "A watercolor lighthouse at dawn";

/** The generated picture, painted inline. Its colours are fixed: it depicts an image, which themes don't recolour. */
function Lighthouse() {
  return (
    <svg className="login-tour-art" viewBox="0 0 300 200" preserveAspectRatio="xMidYMid slice" focusable="false">
      <defs>
        <linearGradient id="login-tour-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#9db3d6" />
          <stop offset="0.36" stopColor="#d6c3d6" />
          <stop offset="0.68" stopColor="#f3c6a6" />
          <stop offset="1" stopColor="#fbe4c4" />
        </linearGradient>
        <linearGradient id="login-tour-sea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#a9bccb" />
          <stop offset="1" stopColor="#65829b" />
        </linearGradient>
        <linearGradient id="login-tour-cliff" x1="0" y1="0" x2="0.4" y2="1">
          <stop offset="0" stopColor="#77836f" />
          <stop offset="1" stopColor="#3f4b47" />
        </linearGradient>
        <linearGradient id="login-tour-beam" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#fff4d2" stopOpacity="0.75" />
          <stop offset="1" stopColor="#fff4d2" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="login-tour-glow">
          <stop offset="0" stopColor="#fff7e6" />
          <stop offset="0.45" stopColor="#ffe6bf" stopOpacity="0.7" />
          <stop offset="1" stopColor="#ffe6bf" stopOpacity="0" />
        </radialGradient>
        <filter id="login-tour-wash" x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="7" />
          <feDisplacementMap in="SourceGraphic" scale="5" />
        </filter>
        <filter id="login-tour-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch" />
          <feColorMatrix values="0 0 0 0 0.42 0 0 0 0 0.33 0 0 0 0 0.24 0 0 0 0.16 0" />
        </filter>
      </defs>
      <rect width="300" height="200" fill="url(#login-tour-sky)" />
      <g filter="url(#login-tour-wash)">
        <circle cx="210" cy="114" r="46" fill="url(#login-tour-glow)" />
        <circle cx="210" cy="116" r="12" fill="#fff3dc" />
        <ellipse cx="96" cy="40" rx="62" ry="7" fill="#f6eef2" opacity="0.45" />
        <ellipse cx="238" cy="64" rx="48" ry="5" fill="#fbe9dc" opacity="0.5" />
        <path d="M170 123q34-11 62-8t68 2v6H170z" fill="#8e98ab" opacity="0.55" />
        <rect y="122" width="300" height="78" fill="url(#login-tour-sea)" />
        <path d="M0 116l30-6q25-6 50-1l20 6q16 6 24 19l10 66H0z" fill="url(#login-tour-cliff)" />
        <path d="M0 117l30-6q25-6 50-1l20 6-3 3-19-6q-22-5-46 1L0 121z" fill="#8d9a74" opacity="0.8" />
      </g>
      <g opacity="0.75">
        <rect x="190" y="127" width="40" height="2.4" rx="1.2" fill="#fde6c8" />
        <rect x="196" y="133" width="28" height="2.2" rx="1.1" fill="#fde6c8" />
        <rect x="201" y="140" width="18" height="2" rx="1" fill="#fde6c8" />
        <rect x="205" y="148" width="10" height="1.8" rx="0.9" fill="#fde6c8" />
        <path
          d="M150 164q12-3 24 0M232 172q14-3 28 0M160 184q10-2 20 0"
          stroke="#e8eef3"
          strokeWidth="1.2"
          fill="none"
        />
      </g>
      <polygon points="64,62 300,34 300,96 64,68" fill="url(#login-tour-beam)" opacity="0.6" />
      <circle cx="62" cy="65" r="16" fill="url(#login-tour-glow)" />
      <polygon points="54.5,112 69.5,112 67,72 57,72" fill="#f7f0e5" />
      <polygon points="54.9,106 69.1,106 68.75,100 55.25,100" fill="#c45a44" />
      <polygon points="55.9,90 68.1,90 67.75,84 56.25,84" fill="#c45a44" />
      <rect x="54.5" y="69" width="15" height="3" rx="1" fill="#3f3a37" />
      <rect x="58" y="61" width="8" height="8" fill="#ffe8a6" stroke="#3f3a37" strokeWidth="0.8" />
      <polygon points="56.5,61 67.5,61 62,55" fill="#b4503c" />
      <rect x="72" y="101" width="16" height="9" fill="#efe5d8" />
      <polygon points="71,101 89,101 84,95.5 76,95.5" fill="#a9503e" />
      <rect width="300" height="200" filter="url(#login-tour-grain)" />
    </svg>
  );
}

function ImageScene({ play }: SceneProps) {
  const send = 1.6;
  return (
    <div className="login-tour-chat">
      <div className="login-tour-thread">
        <Bubble text={PICTURE} at={send + 0.05} />
        <Reply play={play} from={1.8} fill>
          <div className="login-tour-card login-tour-image-card login-tour-in" style={at(1.9)}>
            <div className="login-tour-picture">
              <span className="login-tour-develop" style={at(3.55)}>
                <Lighthouse />
              </span>
              {play ? (
                <span className="login-tour-stripes login-tour-only" style={at(1.9, 3.75)}>
                  <span>
                    <span className="spinner" />
                    Creating the image…
                  </span>
                </span>
              ) : null}
            </div>
            <div className="login-tour-image-bar login-tour-in" style={at(3.8)}>
              <FileTag filename="lighthouse-at-dawn.png" size="sm" />
              <b>lighthouse-at-dawn.png</b>
              <small>1536 × 1024</small>
              <span className="login-tour-icon-btn">
                <DownloadIcon size={13} />
              </span>
            </div>
          </div>
        </Reply>
      </div>
      <Composer play={play} question={PICTURE} start={0.45} send={send} />
    </div>
  );
}

// ---------- 5. Gmail ----------

const INBOX = "What did Alice say about the invoice?";

function InboxScene({ play }: SceneProps) {
  const send = 1.65;
  return (
    <div className="login-tour-chat login-tour-inbox">
      <div className="login-tour-thread">
        <Bubble text={INBOX} at={send + 0.05} />
        <Reply play={play} from={1.85} until={2.15}>
          <Answer start={2.15} cite="1" parts={["She needs the PO number to pay it."]} />
          <Source mail name="Alice Moreno" detail="Re: Invoice 4471" at={2.95} />
        </Reply>
        <div className="login-tour-card login-tour-draft login-tour-in" style={at(3.5)}>
          <div className="login-tour-draft-head">
            <MailIcon size={14} />
            <b>Draft reply</b>
            <span className="login-tour-muted">to Alice</span>
          </div>
          <p>Hi Alice, the PO number is 7731. Thanks, Maya</p>
          <div className="login-tour-draft-actions">
            <span className="btn btn-primary btn-xs">Send</span>
            <span className="btn btn-xs">Edit</span>
            <span className="login-tour-muted">Waits for your OK</span>
          </div>
        </div>
      </div>
      <Composer play={play} question={INBOX} start={0.45} send={send} />
    </div>
  );
}

// ---------- 6. Private and shared ----------

const MINE = ["Northwind.pdf", "Q3-report.pdf", "Payslips.xlsx", "Notes.md"];
const TEAM = ["Handbook.pdf", "Brand-kit.pptx", "Holidays.xlsx", "Price-list.pdf"];

function Shelf({ names, from }: { names: string[]; from: number }) {
  return (
    <ul className="login-tour-shelf">
      {names.map((name, i) => (
        <li key={name} className="login-tour-in" style={at(from + i * 0.14)}>
          <FileTag filename={name} size="sm" />
          <span>{name}</span>
        </li>
      ))}
    </ul>
  );
}

function PrivateScene() {
  return (
    <div className="login-tour-spaces">
      <div className="login-tour-space login-tour-space-mine login-tour-from-left" style={at(0.3)}>
        <div className="login-tour-space-head">
          <span className="login-tour-space-icon login-tour-pop" style={at(1.5)}>
            <LockIcon size={14} />
          </span>
          <b>My files</b>
          <small>Only you</small>
        </div>
        <Shelf names={MINE} from={0.6} />
      </div>
      <div className="login-tour-space login-tour-space-team login-tour-from-right" style={at(0.65)}>
        <div className="login-tour-space-head">
          <span className="login-tour-space-icon">
            <UsersIcon size={14} />
          </span>
          <b>Team library</b>
          <small>Shared</small>
        </div>
        <Shelf names={TEAM} from={0.95} />
      </div>
      <p className="login-tour-both login-tour-in" style={at(2.1)}>
        <SearchIcon size={14} />
        Answers search both
      </p>
    </div>
  );
}

export const TOUR_SCENES: TourScene[] = [
  {
    title: "Cited answers",
    caption: "Every answer points to the passage it came from.",
    duration: 6000,
    Scene: AskScene,
  },
  {
    title: "Drop in any file",
    caption: "PDF, Word, Excel, slides, even scanned pages.",
    duration: 6000,
    Scene: FilesScene,
  },
  {
    title: "Turn answers into files",
    caption: "PDF, Word, Excel or Markdown, saved to your documents.",
    plan: "Standard and up",
    duration: 6000,
    Scene: ReportScene,
  },
  {
    title: "Picture it",
    caption: "Images made in chat, ready for your reports.",
    plan: "Professional",
    duration: 6000,
    Scene: ImageScene,
  },
  {
    title: "Your inbox, on hand",
    caption: "Search, import and draft. Nothing is sent without your OK.",
    plan: "Professional",
    duration: 6500,
    Scene: InboxScene,
  },
  {
    title: "Private by default",
    caption: "Your files stay yours, next to a shared knowledge base.",
    duration: 6000,
    Scene: PrivateScene,
  },
];
