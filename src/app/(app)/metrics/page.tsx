"use client";
import dynamic from "next/dynamic";

const MetricsClient = dynamic(() => import("./metrics-client").then((m) => m.MetricsClient), { ssr: false });

export default function MetricsPage() {
  return <MetricsClient />;
}
