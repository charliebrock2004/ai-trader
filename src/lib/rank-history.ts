/** Each tournament entry's rank on each recorded day, from the `tournament-snapshots` branch. */
export type RankHistory = {
  days: string[];
  ranks: Record<string, (number | null)[]>;
};

export const RANK_HISTORY_URL =
  "https://raw.githubusercontent.com/charliebrock2004/ai-trader/tournament-snapshots/history.json";

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The history if `value` has exactly the expected shape, else null. */
export function parseRankHistory(value: unknown): RankHistory | null {
  if (typeof value !== "object" || value === null) return null;
  const v = value as Record<string, unknown>;
  // Only a record that says it is simulated and not live is shown.
  if (v.live !== false || v.simulated !== true) return null;
  const { days, ranks } = v;
  if (!Array.isArray(days) || !days.every((d) => typeof d === "string" && DAY.test(d))) return null;
  if (typeof ranks !== "object" || ranks === null || Array.isArray(ranks)) return null;
  const out: RankHistory["ranks"] = {};
  for (const [id, list] of Object.entries(ranks)) {
    if (!Array.isArray(list) || list.length !== days.length) return null;
    if (!list.every((r) => r === null || (Number.isInteger(r) && r > 0))) return null;
    out[id] = list as (number | null)[];
  }
  return { days: days as string[], ranks: out };
}

/**
 * Places gained between the last two recorded days (positive = moved up the board).
 * Null when there are fewer than two days or the entry was absent on either.
 */
export function rankMove(history: RankHistory, id: string): number | null {
  const n = history.days.length;
  const list = history.ranks[id];
  if (n < 2 || !list) return null;
  const prev = list[n - 2];
  const last = list[n - 1];
  return prev == null || last == null ? null : prev - last;
}
