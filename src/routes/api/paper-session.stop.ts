import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/api/paper-session/stop")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const { authoriseMutation, unauthorised } = await import("@/lib/api-auth.server");
        const auth = authoriseMutation(request);
        if (!auth.ok) return unauthorised(auth);
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("stop");
      },
    },
  },
});
