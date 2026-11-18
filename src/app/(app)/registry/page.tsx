"use client";
import dynamic from "next/dynamic";

const RegistryClient = dynamic(() => import("./registry-client").then((m) => m.RegistryClient), { ssr: false });

export default function RegistryPage() {
  return <RegistryClient />;
}
