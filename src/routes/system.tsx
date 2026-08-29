import { createFileRoute } from "@tanstack/react-router";
import { ResearchDesk } from "@/components/research-desk";

export const Route = createFileRoute("/system")({ component: System });

function System() {
  return (
    <main className="min-h-screen bg-bg text-fg">
      <ResearchDesk />
    </main>
  );
}
