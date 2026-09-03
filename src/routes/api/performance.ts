import { createFileRoute } from "@tanstack/react-router";

/** Performance and calibration, computed from persisted data only. */
export const Route = createFileRoute("/api/performance")({
  server: {
    handlers: {
      GET: async () => {
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("performance");
      },
    },
  },
});
