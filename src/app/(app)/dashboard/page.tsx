"use client";
import dynamic from "next/dynamic";

const DashboardClient = dynamic(() => import("./dashboard-client").then((m) => m.DashboardClient), { ssr: false });

export default function DashboardPage() {
  return <DashboardClient />;
}
