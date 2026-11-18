"use client";
import dynamic from "next/dynamic";
const SomaAdminClient = dynamic(() => import("./soma-admin-client"), { ssr: false });
export default function SomaAdminPage() { return <SomaAdminClient />; }
