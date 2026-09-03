import { createFileRoute } from "@tanstack/react-router";
import { DecisionsPage } from "@/components/decisions-page";

export const Route = createFileRoute("/decisions")({ component: DecisionsPage });
