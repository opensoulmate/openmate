"use client";
import dynamic from "next/dynamic";
const SoulClient = dynamic(() => import("./soul-client"), { ssr: false });
export default function SoulPage() { return <SoulClient />; }
