"use client";
import dynamic from "next/dynamic";
const ChangelogClient = dynamic(() => import("./changelog-client").then(m => m.ChangelogClient), { ssr: false });
export default function ChangelogPage() { return <ChangelogClient />; }
