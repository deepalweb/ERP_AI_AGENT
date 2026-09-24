const VARIANT_CLASS = {
  primary: "border-transparent bg-accent text-white hover:bg-accent/90",
  secondary: "border-border bg-surface text-foreground-strong hover:border-border-strong",
  ghost: "border-transparent bg-transparent text-foreground hover:text-foreground-strong",
  danger: "border-transparent bg-critical text-white hover:bg-critical/90",
};

const SIZE_CLASS = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-3.5 py-2 text-sm",
};

/** The one shared button primitive -- every screen used to hand-style raw
 * `<button>` tags inline with no shared component at all. `variant="ghost"`
 * covers the old "text link that acts like a button" pattern (e.g. tab
 * switches, "Collapse"); use an actual `<a>`/`<Link>` instead of this for
 * real navigation. */
export default function Button({ variant = "secondary", size = "md", active = false, className = "", children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${SIZE_CLASS[size] ?? SIZE_CLASS.md} ${
        active ? "border-accent-border bg-accent-soft text-accent" : (VARIANT_CLASS[variant] ?? VARIANT_CLASS.secondary)
      } ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
