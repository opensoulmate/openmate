"use client";
import dynamic from "next/dynamic";

const TagsClient = dynamic(() => import("./tags-client").then((m) => m.TagsClient), { ssr: false });

export default function TagsPage() {
  return <TagsClient />;
}
