import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/api/paper-session/start")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const { authoriseMutation, unauthorised } = await import("@/lib/api-auth.server");
        const auth = authoriseMutation(request);
        if (!auth.ok) return unauthorised(auth);
        const { parseBody, StartSessionSchema } = await import("@/lib/api-schema");
        const parsed = await parseBody(request, StartSessionSchema);
        if (!parsed.ok) return parsed.response;
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("start", parsed.data);
      },
    },
  },
});
