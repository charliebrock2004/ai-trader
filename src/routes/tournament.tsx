import { createFileRoute } from "@tanstack/react-router";
import { TournamentPage } from "@/components/tournament-page";

export const Route = createFileRoute("/tournament")({ component: TournamentRoute });

function TournamentRoute() {
  return (
    <main className="min-h-screen bg-bg text-fg">
      <TournamentPage />
    </main>
  );
}
