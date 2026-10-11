// The browser only ever talks to this Next.js server. /api/* is proxied to the Python
// dashboard API (auth, approvals, trading rules), which is never exposed directly.
const API_URL = process.env.API_URL || "http://127.0.0.1:8080";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  // The rewrite proxy drops requests after 30s by default; strategy compiles run for minutes.
  experimental: { proxyTimeout: 900_000 },  // design-chat turns (Opus + a repair round) can run several minutes
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
