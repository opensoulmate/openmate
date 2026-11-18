"use client";
import dynamic from "next/dynamic";

const HealerClient = dynamic(() => import("./healer-client").then((m) => m.HealerClient), { ssr: false });

export default function HealerPage() {
  return <HealerClient />;
}
