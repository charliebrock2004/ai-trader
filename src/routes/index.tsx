import { createFileRoute } from "@tanstack/react-router";
import { AgentHome } from "@/components/agent-home";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return (
    <main className="min-h-screen bg-bg text-fg">
      <AgentHome />
    </main>
  );
}
