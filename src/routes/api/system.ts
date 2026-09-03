import { createFileRoute } from "@tanstack/react-router";

/** Component health. Reports broken components as broken. */
export const Route = createFileRoute("/api/system")({
  server: {
    handlers: {
      GET: async () => {
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        const { mutationsEnabled } = await import("@/lib/api-auth.server");
        const response = await paperEngineCommand("system");
        const body = (await response.json()) as Record<string, unknown>;
        // Presence only — the token itself never reaches the browser.
        return Response.json({ ...body, control_enabled: mutationsEnabled() });
      },
    },
  },
});
