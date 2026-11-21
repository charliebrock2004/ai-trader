import { createFileRoute } from "@tanstack/react-router";

/** Forward paper tournament leaderboard. Read-only and simulated. */
export const Route = createFileRoute("/api/tournament")({
  server: {
    handlers: {
      GET: async () => {
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("tournament");
      },
    },
  },
});
