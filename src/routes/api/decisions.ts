import { createFileRoute } from "@tanstack/react-router";

/** The decision log. Every HOLD and rejection is in here, with its reason. */
export const Route = createFileRoute("/api/decisions")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const rawLimit = Number(url.searchParams.get("limit") ?? 50);
        const limit = Number.isFinite(rawLimit) ? Math.min(200, Math.max(1, rawLimit)) : 50;
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("decisions", {
          limit,
          only_executed: url.searchParams.get("executed") === "1",
        });
      },
    },
  },
});
