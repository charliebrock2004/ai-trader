import { createFileRoute } from "@tanstack/react-router";
import { PerformancePage } from "@/components/performance-page";

export const Route = createFileRoute("/performance")({ component: Performance });

function Performance() {
  return (
    <main className="min-h-screen bg-bg text-fg">
      <PerformancePage />
    </main>
  );
}
