import { createFileRoute } from "@tanstack/react-router";
import { parseRankHistory, RANK_HISTORY_URL } from "@/lib/rank-history";

/** Rank per recorded day, read from the append-only snapshot branch. Read-only and simulated. */
export const Route = createFileRoute("/api/tournament-history")({
  server: {
    handlers: {
      GET: async () => {
        let history = null;
        try {
          const res = await fetch(RANK_HISTORY_URL, { signal: AbortSignal.timeout(8000) });
          if (res.ok) history = parseRankHistory(await res.json());
        } catch {
          // Reported as unavailable below; the leaderboard itself does not depend on this.
        }
        return Response.json(history ? { ok: true, ...history } : { ok: false, days: [], ranks: {} }, {
          headers: { "cache-control": history ? "public, max-age=900" : "no-store" },
        });
      },
    },
  },
});
