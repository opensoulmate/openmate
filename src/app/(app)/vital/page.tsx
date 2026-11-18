"use client";
import dynamic from "next/dynamic";

const VitalClient = dynamic(() => import("./vital-client").then((m) => m.VitalClient), { ssr: false });

export default function VitalPage() {
  return <VitalClient />;
}
