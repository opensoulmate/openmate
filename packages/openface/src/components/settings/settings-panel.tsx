'use client';

import React, { useState } from 'react';
import { cn } from '../../lib/utils';
import { Menu, X } from 'lucide-react';

// ─── Settings Section Definition ──────────────────────────────────
export interface SettingsSection {
  id: string;
  label: string;
  icon: React.ElementType;
  content: React.ReactNode;
}

// ─── SettingsPanel ────────────────────────────────────────────────
export interface SettingsPanelProps {
  sections: SettingsSection[];
  defaultSection?: string;
  className?: string;
}

export function SettingsPanel({ sections, defaultSection, className }: SettingsPanelProps) {
  const [active, setActive] = useState(defaultSection || sections[0]?.id || '');
  const [showSidebar, setShowSidebar] = useState(false);
  const current = sections.find((s) => s.id === active) || sections[0];

  return (
    <div className={cn("flex h-full", className)}>
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex w-52 shrink-0 flex-col border-r border-border bg-card/30 overflow-y-auto">
        <div className="flex-1 py-2 px-2 space-y-0.5">
          {sections.map((s) => (
            <button key={s.id} onClick={() => setActive(s.id)}
              className={cn("w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors", active === s.id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-muted/50")}>
              <s.icon size={14} className="shrink-0" />
              <span className="truncate">{s.label}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* Mobile sidebar overlay */}
      {showSidebar && (
        <>
          <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setShowSidebar(false)} />
          <aside className="fixed left-0 top-0 bottom-0 w-64 bg-card border-r border-border z-50 lg:hidden flex flex-col">
            <div className="flex items-center justify-between p-3 border-b border-border">
              <span className="text-sm font-medium">设置</span>
              <button onClick={() => setShowSidebar(false)} className="p-1 hover:bg-muted/50 rounded"><X size={16} /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
              {sections.map((s) => (
                <button key={s.id} onClick={() => { setActive(s.id); setShowSidebar(false); }}
                  className={cn("w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors", active === s.id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-muted/50")}>
                  <s.icon size={14} className="shrink-0" />
                  <span className="truncate">{s.label}</span>
                </button>
              ))}
            </div>
          </aside>
        </>
      )}

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        {/* Mobile top bar */}
        <div className="lg:hidden flex items-center gap-2 px-3 py-2 border-b border-border">
          <button onClick={() => setShowSidebar(true)} className="p-1.5 hover:bg-muted/50 rounded-md"><Menu size={16} /></button>
          <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <span>{current?.label}</span>
          </div>
        </div>

        <div className="px-3 lg:px-6 py-4 lg:py-6 space-y-4 lg:space-y-6 max-w-4xl">
          {current?.content}
        </div>
      </main>
    </div>
  );
}
