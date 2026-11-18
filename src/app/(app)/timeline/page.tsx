"use client";
import dynamic from "next/dynamic";
const TimelineClient = dynamic(() => import("./timeline-client").then((m) => m.TimelineClient), { ssr: false });
export default function TimelinePage() {
  return <TimelineClient />;
}
