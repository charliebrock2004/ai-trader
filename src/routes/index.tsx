import { createFileRoute } from "@tanstack/react-router";
import { TradingHome } from "@/components/trading-home";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return (
    <main className="min-h-screen bg-bg text-fg">
      <TradingHome />
    </main>
  );
}
