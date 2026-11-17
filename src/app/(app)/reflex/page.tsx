"use client";
import dynamic from "next/dynamic";

const ReflexClient = dynamic(() => import("./reflex-client").then((m) => m.ReflexClient), { ssr: false });

export default function ReflexPage() {
  return <ReflexClient />;
}
