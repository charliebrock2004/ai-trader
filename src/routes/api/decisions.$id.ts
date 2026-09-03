import { createFileRoute } from "@tanstack/react-router";

/** One decision in full: inputs, probabilities, analyst, verdicts, outcome. */
export const Route = createFileRoute("/api/decisions/$id")({
  server: {
    handlers: {
      GET: async ({ params }) => {
        const id = Number(params.id);
        if (!Number.isInteger(id) || id <= 0) {
          return Response.json(
            { ok: false, error: "A positive integer decision id is required." },
            { status: 400 },
          );
        }
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        return paperEngineCommand("decision", { id });
      },
    },
  },
});
