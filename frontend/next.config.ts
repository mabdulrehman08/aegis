import type { NextConfig } from "next";

// Same-origin proxy to the FastAPI backend (it intentionally has no CORS).
// The WebSocket connects directly to the backend; see src/lib/api.ts.
const backend = process.env.AEGIS_BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/aegis-api/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
