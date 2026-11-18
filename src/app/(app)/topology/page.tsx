"use client";
import dynamic from "next/dynamic";
const TopologyClient = dynamic(() => import("./topology-client").then((m) => m.TopologyClient), { ssr: false });
export default function TopologyPage() {
  return <TopologyClient />;
}
