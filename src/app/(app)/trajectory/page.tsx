"use client";
import dynamic from "next/dynamic";

const TrajectoryClient = dynamic(() => import("./trajectory-client").then((m) => m.TrajectoryClient), { ssr: false });
const EvolutionTimeline = dynamic(() => import("./evolution-timeline").then((m) => m.EvolutionTimeline), { ssr: false });

import { useState } from "react";
import { Activity, BarChart3 } from "lucide-react";

export default function TrajectoryPage() {
  const [tab, setTab] = useState<"trajectory" | "evolution">("trajectory")

  return (
    <div className="h-full flex flex-col">
      {/* Tab bar */}
      <div className="flex gap-1 px-3 lg:px-5 pt-3 lg:pt-4 border-b border-border">
        {[
          { key: "trajectory" as const, label: "轨迹追踪", icon: BarChart3 },
          { key: "evolution" as const, label: "自我进化", icon: Activity },
        ].map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg transition-colors ${
              tab === t.key
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-auto">
        {tab === "trajectory" ? <TrajectoryClient /> : <EvolutionTimeline />}
      </div>
    </div>
  );
}
