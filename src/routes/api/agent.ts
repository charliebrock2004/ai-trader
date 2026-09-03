import { createFileRoute } from "@tanstack/react-router";

/** Read-only agent status. Safe to poll; no authorisation required. */
export const Route = createFileRoute("/api/agent")({
  server: {
    handlers: {
      GET: async () => {
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("agent");
      },
    },
  },
});
