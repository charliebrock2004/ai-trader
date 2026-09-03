import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/api/paper-session/grok")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const { authoriseMutation, unauthorised } = await import("@/lib/api-auth.server");
        const auth = authoriseMutation(request);
        if (!auth.ok) return unauthorised(auth);
        const { analyzePaperBar } = await import("@/lib/paper-inline.server");
        let payload: Record<string, unknown> = {};
        try {
          payload = (await request.json()) as Record<string, unknown>;
        } catch {
          payload = {};
        }
        const result = await analyzePaperBar(payload);
        return Response.json({ ...result, live: false, broker: "NOT USED" });
      },
    },
  },
});
