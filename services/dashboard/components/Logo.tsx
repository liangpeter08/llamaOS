/** Flowing llama artwork tinted with the active dashboard accent. */
export function Logo({ size = 32, title = "llamaOS" }: { size?: number; title?: string }) {
  return (
    <span
      role="img"
      aria-label={title}
      style={{
        width: size,
        height: size,
        flex: "none",
        display: "block",
        backgroundColor: "var(--accent)",
        mask: 'url("/llama-logo-v2.png") center / contain no-repeat',
        WebkitMask: 'url("/llama-logo-v2.png") center / contain no-repeat',
      }}
    />
  );
}
