"use client";
import dynamic from "next/dynamic";

const PermissionClient = dynamic(() => import("./permission-client").then((m) => m.PermissionClient), { ssr: false });

export default function PermissionPage() {
  return <PermissionClient />;
}
