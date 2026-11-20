'use client';

// L2Layout — 左栏统一布局模板（用户令 2026-11-20）：
// "把25个组件的布局都调整一下，整个布局风格要保持一致" + "所有页面左栏用同一套布局模板
// （同宽、同分组样式、同间距、同滚动行为），不许各页各画一套"。
//
// 左栏只许三种形态（用户铁律："左侧为二级菜单"=当前一级自己的二级清单）：
//   ① 器官页 = 25 器官一个不多一个不少（零工具零其他内容）
//   ② 各域页 = 该域原生子功能（下载/学习等原有 sidebar，pageSidebar 原样）
//   ③ 叶子页 = 无侧栏
// 三种形态共用本模板的容器/分组/行样式 token；禁止发明拼接块/二级菜单块。

import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** 统一滚动容器（同滚动行为：整栏单滚动区） */
export const L2_SHELL_CLASS = "flex flex-col h-full min-h-0 overflow-y-auto";
/** 统一列表间距 */
export const L2_LIST_CLASS = "flex flex-col gap-3 py-1";
/** 统一分组标题样式 */
export const L2_GROUP_CLASS =
  "px-3 pb-1 pt-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground";
/** 统一行样式（icon + 名称 + 弱化副签 + 右侧槽） */
export const L2_ROW_BASE =
  "no-underline flex items-center gap-2 rounded px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground";
export const L2_ROW_ACTIVE =
  "bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary";

export function L2List({ children }: { children: React.ReactNode }) {
  return <div className={L2_LIST_CLASS}>{children}</div>;
}

export function L2Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={L2_GROUP_CLASS}>{title}</div>
      <div className="flex flex-col">{children}</div>
    </div>
  );
}

export function L2Row({
  href,
  icon: Icon,
  label,
  tag,
  active,
  right,
}: {
  href: string;
  icon: LucideIcon;
  label: string;
  tag?: string;
  active?: boolean;
  right?: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cn(L2_ROW_BASE, active && L2_ROW_ACTIVE)}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{label}</span>
      {tag && (
        <span className="shrink-0 text-[10px] text-muted-foreground/60">{tag}</span>
      )}
      {right}
    </Link>
  );
}
