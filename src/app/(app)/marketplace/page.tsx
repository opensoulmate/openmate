"use client";
import dynamic from "next/dynamic";

const MarketplaceClient = dynamic(() => import("./marketplace-client").then((m) => m.MarketplaceClient), { ssr: false });

export default function MarketplacePage() {
  return <MarketplaceClient />;
}
