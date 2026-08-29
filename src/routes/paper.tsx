import { createFileRoute, Navigate } from "@tanstack/react-router";

export const Route = createFileRoute("/paper")({ component: Paper });

function Paper() {
  return <Navigate to="/" />;
}
