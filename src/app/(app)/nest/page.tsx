"use client";
import dynamic from "next/dynamic";
const NestClient = dynamic(() => import("./nest-client").then(m => m.NestClient), { ssr: false });
export default function NestPage() { return <NestClient />; }
