import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/api/paper-session")({
  server: {
    handlers: {
      GET: async () => {
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("status");
      },
    },
  },
});
