const SIZE_CLASS: Record<AvatarSize, string> = { 32: "ui-avatar", 40: "ui-avatar ui-avatar-lg", 56: "ui-avatar ui-avatar-xl" };

export type AvatarSize = 32 | 40 | 56;

/** Up to two initials: the first letters of the first two parts split on . _ - or space; else the first letter. */
export function initials(name: string): string {
  const parts = name.split(/[._\-\s]+/).filter(Boolean);
  const letters = (parts.length > 0 ? parts.slice(0, 2) : [name]).map((part) => Array.from(part)[0] ?? "");
  return letters.join("").toUpperCase();
}

/** The user's initials in a warm circle (32 px; 40 in menu heads, 56 in Settings → Account). Decorative. */
export default function Avatar({ name, size = 32 }: { name: string; size?: AvatarSize }) {
  return (
    <span className={SIZE_CLASS[size]} aria-hidden="true">
      {initials(name)}
    </span>
  );
}
