import { createFileRoute } from "@tanstack/react-router";

/** Component health. Reports broken components as broken. */
export const Route = createFileRoute("/api/system")({
  server: {
    handlers: {
      GET: async () => {
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        const { mutationsEnabled, frontendIsOpen } = await import("@/lib/api-auth.server");
        const { remoteWorkerConfigured } = await import("@/lib/worker-remote.server");
        const response = await paperEngineCommand("system");
        const body = (await response.json()) as Record<string, unknown>;
        // Presence and shape only. No token and no worker URL reaches the
        // browser — the URL is deployment detail, and a booleanised answer is
        // all the dashboard needs to tell the operator the truth.
        return Response.json(
          {
            ...body,
            control_enabled: mutationsEnabled(),
            worker_connected: remoteWorkerConfigured(),
            frontend_open: frontendIsOpen(),
          },
          { headers: { "cache-control": "no-store" } },
        );
      },
    },
  },
});
