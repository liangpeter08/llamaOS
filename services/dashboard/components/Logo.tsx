import { useId } from "react";

/** llamaOS mark: a llama whose ears are two rising candlesticks. Same artwork as public/logo.svg. */
export function Logo({ size = 32, title = "llamaOS" }: { size?: number; title?: string }) {
  const id = useId().replace(/:/g, "");
  const [bg, gold, clip, eye] = ["bg", "gold", "clip", "eye"].map((k) => `${k}-${id}`);
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label={title} style={{ flex: "none", display: "block" }}>
      <defs>
        <linearGradient id={bg} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#17223b" />
          <stop offset="1" stopColor="#0a0f1c" />
        </linearGradient>
        <linearGradient id={gold} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#f0dfb2" />
          <stop offset="1" stopColor="#c49a52" />
        </linearGradient>
        <clipPath id={clip}><rect width="64" height="64" rx="15" /></clipPath>
        <mask id={eye}>
          <rect width="64" height="64" fill="#fff" />
          <circle cx="37.4" cy="27.6" r="1.5" fill="#000" />
        </mask>
      </defs>
      <rect width="64" height="64" rx="15" fill={`url(#${bg})`} />
      <g clipPath={`url(#${clip})`}>
        <g transform="rotate(-14 27.6 27)">
          <rect x="27" y="9.6" width="1.3" height="6" rx="0.65" fill="#f4efe6" />
          <rect x="25.25" y="14" width="4.8" height="13.5" rx="2.4" fill="#f4efe6" />
        </g>
        <g transform="rotate(-5 33.8 26)">
          <rect x="33.15" y="4" width="1.3" height="6.4" rx="0.65" fill={`url(#${gold})`} />
          <rect x="31.4" y="8.8" width="4.8" height="17.5" rx="2.4" fill={`url(#${gold})`} />
        </g>
        <path mask={`url(#${eye})`} fill="#f4efe6"
          d="M19.5 67 C19.5 53 19.2 42 20.8 34.5 C21.8 29.5 24 25.6 28 23.4 C30.6 21.9 34 21.6 37.4 22.6 C40.8 23.6 43.6 25.4 45.6 27.6 C47.6 29.8 46.9 33.2 44 33.8 C41 34.4 38.2 34.6 36 35.4 C34 36.1 33.2 38 33.2 41.5 C33.2 49 34 57 35.2 67 Z" />
      </g>
      <rect x="0.5" y="0.5" width="63" height="63" rx="14.5" fill="none" stroke="#ffffff" strokeOpacity="0.07" />
    </svg>
  );
}
