import type { NextConfig } from "next";
import { devApiOrigin } from "./lib/dev-api-origin.ts";

const developmentApiOrigin = process.env.NODE_ENV === "development"
  ? devApiOrigin(process.env.TSRP_DEV_API_ORIGIN)
  : null;
const pythonApiRoutes = ["analyze", "movers", "top_stocks", "scenario", "search", "snapshot_diff"];

const nextConfig: NextConfig = {
  agentRules: false,
  devIndicators: false,
  poweredByHeader: false,
  async rewrites() {
    return developmentApiOrigin ? {
      afterFiles: pythonApiRoutes.map((route) => ({
        source: `/api/${route}`,
        destination: `${developmentApiOrigin}/api/${route}`,
      })),
    } : [];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          ...(process.env.NODE_ENV === "production" ? [
            { key: "Strict-Transport-Security", value: "max-age=31536000" },
          ] : []),
        ],
      },
      {
        source: "/api/auth/callback/:path*",
        headers: [
          { key: "Cache-Control", value: "private, no-store" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
