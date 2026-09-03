import { createFileRoute, useParams } from "@tanstack/react-router";
import { DecisionDetailPage } from "@/components/decision-detail";

function DecisionRoute() {
  const { id } = useParams({ from: "/decisions/$id" });
  return <DecisionDetailPage id={id} />;
}

export const Route = createFileRoute("/decisions/$id")({ component: DecisionRoute });
