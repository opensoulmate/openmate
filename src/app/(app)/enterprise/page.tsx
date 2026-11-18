"use client";
import dynamic from "next/dynamic";
const EnterpriseClient = dynamic(() => import("./enterprise-client").then((m) => m.EnterpriseClient), { ssr: false });
export default function EnterprisePage() {
  return <EnterpriseClient />;
}
