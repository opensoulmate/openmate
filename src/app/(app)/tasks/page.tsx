"use client";
import dynamic from "next/dynamic";

const TasksClient = dynamic(
  () => import("./tasks-client").then((m) => m.TasksClient),
  { ssr: false }
);

export default function TasksPage() {
  return <TasksClient />;
}
