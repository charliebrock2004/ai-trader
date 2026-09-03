import { createFileRoute } from "@tanstack/react-router";

/**
 * Run one agent cycle. Mutating, so it requires the shared secret.
 *
 * A cycle spends model budget and writes to the audit trail, which is exactly
 * why it is not open to anyone holding the URL.
 */
export const Route = createFileRoute("/api/agent/cycle")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const { authoriseMutation, unauthorised } = await import("@/lib/api-auth.server");
        const auth = authoriseMutation(request);
        if (!auth.ok) return unauthorised(auth);
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("cycle");
      },
    },
  },
});
