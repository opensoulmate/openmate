"use client";
import dynamic from "next/dynamic";

const AiEngineClient = dynamic(() => import("./ai-engine-client").then((m) => m.AiEngineClient), { ssr: false });

export default function AiEnginePage() {
  return <AiEngineClient />;
}
