"use client";
import dynamic from "next/dynamic";
const WorkflowClient = dynamic(() => import("./workflow-client").then((m) => m.WorkflowClient), { ssr: false });
export default function WorkflowPage() {
  return <WorkflowClient />;
}
