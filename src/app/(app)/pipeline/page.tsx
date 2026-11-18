"use client";
import dynamic from "next/dynamic";
const PipelineClient = dynamic(() => import("./pipeline-client").then(m => m.PipelineClient), { ssr: false });
export default function PipelinePage() { return <PipelineClient />; }
