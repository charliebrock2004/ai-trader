import { createFileRoute } from "@tanstack/react-router";
import { SystemPage } from "@/components/system-page";

export const Route = createFileRoute("/system")({ component: System });

function System() {
  return (
    <main className="min-h-screen bg-bg text-fg">
      <SystemPage />
    </main>
  );
}
