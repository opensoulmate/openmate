"use client";
import dynamic from "next/dynamic";

const SkillsClient = dynamic(() => import("./skills-client").then((m) => m.SkillsClient), { ssr: false });

export default function SkillsPage() {
  return <SkillsClient />;
}
