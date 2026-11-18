"use client";
import dynamic from "next/dynamic";

const IntelligenceClient = dynamic(() => import("./intelligence-client").then((m) => m.IntelligenceClient), { ssr: false });

export default function IntelligencePage() {
  return <IntelligenceClient />;
}
