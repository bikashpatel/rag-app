import type { ReactNode } from "react";

// The shared stroke icons of the shell, Settings and the primitives (visual spec: 1.75 stroke, currentColor, 18 px
// by default, 15 in small buttons). Decorative: aria-hidden; the control carries the name.

export type IconProps = { size?: number; className?: string };

function Icon({ size = 18, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

const icon = (paths: ReactNode) =>
  function StrokeIcon(props: IconProps) {
    return <Icon {...props}>{paths}</Icon>;
  };

export const MenuIcon = icon(<path d="M4 6.5h16M4 12h16M4 17.5h11" />);
export const NewChatIcon = icon(
  <>
    <path d="M12 4H7a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-5" />
    <path d="M17.6 3.9a2 2 0 0 1 2.8 2.8l-7.9 7.9-3.5.9.9-3.5z" />
  </>,
);
export const ChevronDownIcon = icon(<path d="m6 9 6 6 6-6" />);
export const ChevronRightIcon = icon(<path d="m9 6 6 6-6 6" />);
export const ChevronLeftIcon = icon(<path d="m15 6-6 6 6 6" />);
export const PanelRightIcon = icon(
  <>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
    <path d="M14.5 4.5v15M17 8.5h1M17 11h1" />
  </>,
);
export const PlusIcon = icon(<path d="M12 5v14M5 12h14" />);
export const PaperclipIcon = icon(
  <path d="m20 11.5-8.2 8.2a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9" />,
);
export const ImageIcon = icon(
  <>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
    <circle cx="9" cy="10" r="1.75" />
    <path d="m20.5 15.5-4.6-4.6a1.5 1.5 0 0 0-2.1 0L5 19.5" />
  </>,
);
export const FileIcon = icon(
  <>
    <path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z" />
    <path d="M14 3.5V8h4.5M9 13h6M9 16.5h4" />
  </>,
);
export const DocsIcon = icon(
  <>
    <path d="M9 6.5V5.5a2 2 0 0 1 2-2h5l4 4v9a2 2 0 0 1-2 2h-1" />
    <path d="M13 6.5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2v-10a2 2 0 0 0-2-2z" />
    <path d="M7 12h5M7 15.5h3.5" />
  </>,
);
export const MailIcon = icon(
  <>
    <rect x="3" y="5" width="18" height="14" rx="2.5" />
    <path d="m3.5 7.5 8.5 6 8.5-6" />
  </>,
);
export const SettingsIcon = icon(
  <>
    <path d="M12.22 2.5h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73v.18a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4.5a2 2 0 0 0-2-2z" />
    <circle cx="12" cy="12" r="3" />
  </>,
);
export const SlidersIcon = icon(
  <>
    <path d="M4.5 7h9M17.5 7h2M4.5 17h3M11.5 17h8" />
    <circle cx="15.5" cy="7" r="2" />
    <circle cx="9.5" cy="17" r="2" />
  </>,
);
export const ShieldIcon = icon(
  <>
    <path d="M12 3.5 5 6v5.5c0 4.4 3 7.9 7 9 4-1.1 7-4.6 7-9V6z" />
    <path d="m9.3 12 1.9 1.9 3.5-3.7" />
  </>,
);
export const LogoutIcon = icon(
  <>
    <path d="M14 4.5h3.5a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H14" />
    <path d="M9.5 16.5 5 12l4.5-4.5M5 12h10" />
  </>,
);
export const UserIcon = icon(
  <>
    <circle cx="12" cy="8.5" r="3.75" />
    <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
  </>,
);
export const UsersIcon = icon(
  <>
    <circle cx="9" cy="8.5" r="3.5" />
    <path d="M2.5 19.5a6.5 6.5 0 0 1 13 0M15.5 5.2a3.5 3.5 0 0 1 0 6.6M18 13.6a6.5 6.5 0 0 1 3.5 5.9" />
  </>,
);
export const ChartIcon = icon(<path d="M4 20h16M7 16.5v-4M12 16.5v-10M17 16.5v-7" />);
export const PlugIcon = icon(
  <>
    <path d="M9 3.5v4M15 3.5v4" />
    <path d="M6.5 7.5h11v3.5a5.5 5.5 0 0 1-11 0z" />
    <path d="M12 16.5v4" />
  </>,
);
export const ArrowUpIcon = icon(<path d="M12 19V5.5M6.5 11 12 5.5l5.5 5.5" />);
export const ArrowDownIcon = icon(<path d="M12 5v13.5M6.5 13l5.5 5.5 5.5-5.5" />);
export const ArrowRightIcon = icon(<path d="M5 12h13.5M13 6.5l5.5 5.5-5.5 5.5" />);
export const ArrowLeftIcon = icon(<path d="M19 12H5.5M11 6.5 5.5 12l5.5 5.5" />);
export const CopyIcon = icon(
  <>
    <rect x="8.5" y="8.5" width="11" height="11" rx="2.5" />
    <path d="M15.5 8.5v-2a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" />
  </>,
);
export const CheckIcon = icon(<path d="m5 12.5 4.5 4.5L19 7.5" />);
export const CheckCircleIcon = icon(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="m8.5 12.2 2.4 2.4 4.6-4.9" />
  </>,
);
export const DownloadIcon = icon(<path d="M12 4v11M7 10.5l5 5 5-5M5 19.5h14" />);
export const UploadIcon = icon(
  <>
    <path d="M12 15V4.5M7.5 9 12 4.5 16.5 9" />
    <path d="M4.5 14.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
  </>,
);
export const MoreIcon = icon(
  <>
    <circle cx="6" cy="12" r="1.1" fill="currentColor" />
    <circle cx="12" cy="12" r="1.1" fill="currentColor" />
    <circle cx="18" cy="12" r="1.1" fill="currentColor" />
  </>,
);
export const CloseIcon = icon(<path d="M18 6 6 18M6 6l12 12" />);
export const RefreshIcon = icon(
  <>
    <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9" />
    <path d="M4.5 4.5V9H9" />
  </>,
);
export const SunIcon = icon(
  <>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4" />
  </>,
);
export const MoonIcon = icon(<path d="M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10z" />);
export const MonitorIcon = icon(
  <>
    <rect x="3" y="4.5" width="18" height="12" rx="2.5" />
    <path d="M8.5 20h7M12 16.5V20" />
  </>,
);
export const LaptopIcon = icon(
  <>
    <rect x="4.5" y="5" width="15" height="10.5" rx="2" />
    <path d="M2.5 19h19" />
  </>,
);
export const PhoneIcon = icon(
  <>
    <rect x="6.5" y="3" width="11" height="18" rx="2.5" />
    <path d="M11 17.5h2" />
  </>,
);
export const TabletIcon = icon(
  <>
    <rect x="4.5" y="3" width="15" height="18" rx="2.5" />
    <path d="M11 17.5h2" />
  </>,
);
export const KeyIcon = icon(
  <>
    <circle cx="8" cy="15.5" r="4" />
    <path d="m11 12.5 8.5-8.5M16 7.5l2.5 2.5M13.5 10l2 2" />
  </>,
);
export const SparkleIcon = icon(
  <path
    d="M12 3.5c.6 4.6 2.6 7 7.5 8.5-4.9 1.5-6.9 3.9-7.5 8.5-.6-4.6-2.6-7-7.5-8.5 4.9-1.5 6.9-3.9 7.5-8.5z"
    fill="currentColor"
    stroke="none"
  />,
);
export const SearchIcon = icon(
  <>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </>,
);
export const TrashIcon = icon(
  <path d="M4.5 7h15M9.5 7V5a1.5 1.5 0 0 1 1.5-1.5h2A1.5 1.5 0 0 1 14.5 5v2M6.5 7l.8 11.6a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9L17.5 7" />,
);
/** Warning: a triangle (meters at 90%, warning notices). */
export const AlertIcon = icon(
  <>
    <path d="M10.3 4.6 2.9 17.5a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0z" />
    <path d="M12 9.5v4M12 17h.01" />
  </>,
);
/** Danger: a circle (meters at 100%, errors). */
export const AlertCircleIcon = icon(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5v5M12 16h.01" />
  </>,
);
export const InfoIcon = icon(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5M12 8h.01" />
  </>,
);
export const LockIcon = icon(
  <>
    <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
    <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
  </>,
);
export const EyeIcon = icon(
  <>
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
    <circle cx="12" cy="12" r="3" />
  </>,
);
export const EyeOffIcon = icon(
  <>
    <path d="M3.5 3.5l17 17" />
    <path d="M10.6 5.6A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.6 3.4M6.6 6.6C3.9 8.3 2.5 12 2.5 12S6 18.5 12 18.5a9 9 0 0 0 4.7-1.3" />
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </>,
);
export const ExternalLinkIcon = icon(
  <>
    <path d="M14 4.5h5.5V10M19.5 4.5 11 13" />
    <path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4" />
  </>,
);
export const ClockIcon = icon(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </>,
);
export const CalendarIcon = icon(
  <>
    <rect x="4" y="5.5" width="16" height="14.5" rx="2.5" />
    <path d="M8 3.5v4M16 3.5v4M4 10h16" />
  </>,
);
export const BookIcon = icon(
  <>
    <path d="M4.5 18.5V5.5a2 2 0 0 1 2-2h13v13H6.5a2 2 0 0 0-2 2 2 2 0 0 0 2 2h13" />
    <path d="M8.5 7.5h7" />
  </>,
);
export const QuoteIcon = icon(
  <>
    <path d="M5 18.5V13a5.5 5.5 0 0 1 4-5.3M14 18.5V13a5.5 5.5 0 0 1 4-5.3" />
    <path d="M5 13h4v5.5H5zM14 13h4v5.5h-4z" />
  </>,
);
export const CpuIcon = icon(
  <>
    <rect x="6.5" y="6.5" width="11" height="11" rx="2" />
    <path d="M9.5 3.5v3M14.5 3.5v3M9.5 17.5v3M14.5 17.5v3M3.5 9.5h3M3.5 14.5h3M17.5 9.5h3M17.5 14.5h3" />
  </>,
);
export const PenIcon = icon(<path d="M15.5 4.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z" />);
export const StopIcon = icon(<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none" />);
export const PauseIcon = icon(
  <>
    <rect x="6.5" y="5.5" width="4" height="13" rx="1.2" fill="currentColor" stroke="none" />
    <rect x="13.5" y="5.5" width="4" height="13" rx="1.2" fill="currentColor" stroke="none" />
  </>,
);
export const PlayIcon = icon(<path d="M8 6.2v11.6a1 1 0 0 0 1.5.9l9.3-5.8a1 1 0 0 0 0-1.8L9.5 5.3A1 1 0 0 0 8 6.2z" fill="currentColor" />);

// Plans and billing (docs/briefs/plans-billing-visual.md 2.2).
export const CardIcon = icon(
  <>
    <rect x="3" y="5.5" width="18" height="13" rx="2.5" />
    <path d="M3 10h18M7 14.5h3.5" />
  </>,
);
export const TagIcon = icon(
  <>
    <path d="M3.5 12V5a1.5 1.5 0 0 1 1.5-1.5h7a1.5 1.5 0 0 1 1.06.44l7.5 7.5a1.5 1.5 0 0 1 0 2.12l-7 7a1.5 1.5 0 0 1-2.12 0l-7.5-7.5A1.5 1.5 0 0 1 3.5 12z" />
    <circle cx="8.5" cy="8.5" r="1.5" />
  </>,
);
export const ReceiptIcon = icon(
  <>
    <path d="M6.5 3.5h11v17l-2.2-1.4-1.9 1.4-1.9-1.4-1.9 1.4-1.9-1.4-1.2 1z" />
    <path d="M9.5 8h5M9.5 11.5h5M9.5 15h3" />
  </>,
);
export const BankIcon = icon(<path d="M3.5 9 12 4.5 20.5 9M5.5 9.5v7.5M10 9.5v7.5M14 9.5v7.5M18.5 9.5v7.5M3.5 20h17" />);
export const GiftIcon = icon(
  <>
    <rect x="3.5" y="8.5" width="17" height="4" rx="1" />
    <path d="M5 12.5v6a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5v-6M12 8.5V20M12 8.5c-1.6-3.6-5.2-4-5-1.6.1 1.4 2.6 1.6 5 1.6zM12 8.5c1.6-3.6 5.2-4 5-1.6-.1 1.4-2.6 1.6-5 1.6z" />
  </>,
);
export const MinusIcon = icon(<path d="M6.5 12h11" />);
export const ArrowUpRightIcon = icon(<path d="M7 17 17 7M8.5 7H17v8.5" />);
export const FlaskIcon = icon(
  <>
    <path d="M9.5 3.5h5M10 3.5v5.2L4.9 17.6A2 2 0 0 0 6.6 20.5h10.8a2 2 0 0 0 1.7-2.9L14 8.7V3.5" />
    <path d="M7.3 14.5h9.4" />
  </>,
);
