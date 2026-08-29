import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/api/paper-session/start")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const { paperEngineCommand } = await import("@/lib/paper-engine.server");
        let payload: Record<string, unknown> = {
          symbol: "BTC-USD",
          source: "public",
          bars: 24,
          timeframe: "5m",
          grok_frequency: 8,
          warmup: 8,
          continuous: true,
        };
        try {
          const body = (await request.json()) as Record<string, unknown>;
          payload = { ...payload, ...body };
        } catch {
          /* use defaults */
        }
        return paperEngineCommand("start", payload);
      },
    },
  },
});
