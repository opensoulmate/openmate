"use client";
import dynamic from "next/dynamic";

const HeredityClient = dynamic(
  () => import("./heredity-client").then((m) => m.HeredityClient),
  { ssr: false }
);

export default function HeredityPage() {
  return <HeredityClient />;
}
