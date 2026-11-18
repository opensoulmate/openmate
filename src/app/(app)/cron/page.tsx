"use client";
import dynamic from "next/dynamic";

const CronClient = dynamic(() => import("./cron-client").then((m) => m.CronClient), { ssr: false });

export default function CronPage() {
  return <CronClient />;
}
