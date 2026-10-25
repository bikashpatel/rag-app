const ringStyle = (size: number) => ({ width: size, height: size, borderWidth: size >= 20 ? 2.25 : undefined });

/** A ring in the text colour (currentColor), turning; still (but visible) under reduced motion. */
export default function Spinner({ size = 16, label = "Loading" }: { size?: number; label?: string }) {
  return <span className="spinner" style={ringStyle(size)} role="status" aria-label={label} />;
}

/** The spinner of a busy button: hidden from assistive technology, so the button keeps its name (it sets aria-busy). */
export function ButtonSpinner({ size }: { size: number }) {
  return <span className="spinner" style={ringStyle(size)} aria-hidden="true" />;
}
