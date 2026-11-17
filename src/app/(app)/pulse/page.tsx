"use client";
import dynamic from "next/dynamic";

const PulseClient = dynamic(
  () => import("./pulse-client").then((m) => m.PulseClient),
  { ssr: false }
);

export default function PulsePage() {
  return <PulseClient />;
}
