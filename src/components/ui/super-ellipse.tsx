"use client";

/**
 * 小米 Alive 超椭圆组件
 * 公式：|x/a|^n + |y/b|^n = 1，n=3，a=b（正方形）
 */
import { useMemo, useState, type ReactNode } from "react";

interface SuperEllipseProps {
  /** 尺寸（px），正方形边长 */
  size?: number;
  /** 背景色 */
  fill?: string;
  /** 子元素 */
  children?: ReactNode;
  /** 额外 class */
  className?: string;
  /** 点击事件 */
  onClick?: () => void;
}

/** 生成 n=3 超椭圆 SVG path */
function genPath(r: number, n = 3, segments = 72): string {
  const pts: string[] = [];
  for (let i = 0; i <= segments; i++) {
    const theta = (i / segments) * Math.PI * 2;
    const ct = Math.cos(theta);
    const st = Math.sin(theta);
    const x = r + r * Math.sign(ct) * Math.pow(Math.abs(ct), 2 / n);
    const y = r + r * Math.sign(st) * Math.pow(Math.abs(st), 2 / n);
    pts.push(`${x.toFixed(2)},${y.toFixed(2)}`);
  }
  return `M${pts.join(" L")} Z`;
}

export function SuperEllipse({
  size = 40,
  fill = "transparent",
  children,
  className,
  onClick,
}: SuperEllipseProps) {
  const r = size / 2;
  const d = useMemo(() => genPath(r), [r]);

  return (
    <div
      className={className}
      onClick={onClick}
      style={{
        position: "relative",
        width: size,
        height: size,
        flexShrink: 0,
      }}
    >
      {/* 超椭圆背景 */}
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        style={{ position: "absolute", inset: 0 }}
      >
        <path d={d} fill={fill} />
      </svg>
      {/* 内容层 */}
      <div
        style={{
          position: "relative",
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/**
 * 超椭圆头像（全局设计语言）——logo/emoji/图标统一超椭圆裁切、固定 size 视觉一致。
 * src 传图片 URL/路径/dataURI 或 emoji 字符串；图片加载失败自动回落 fallback。
 */
export function SuperAvatar({
  src,
  size = 32,
  alt = "",
  bg = "bg-muted",
  fallback,
  className,
}: {
  src?: string | null;
  size?: number;
  alt?: string;
  /** 无图/加载失败时的底色 class（字面量传入） */
  bg?: string;
  /** 无 src 或图片加载失败时的兜底节点（图标/emoji） */
  fallback?: ReactNode;
  className?: string;
}) {
  const [err, setErr] = useState(false);
  const d = useMemo(() => genPath(size / 2), [size]);
  const isImg = !!src && !err && (src.startsWith("http") || src.startsWith("/") || src.startsWith("data:"));
  return (
    <div
      className={`shrink-0 flex items-center justify-center overflow-hidden ${bg} ${className ?? ""}`}
      style={{ width: size, height: size, clipPath: `path('${d}')` }}
    >
      {isImg ? (
        <img src={src!} alt={alt} className="w-full h-full object-cover" onError={() => setErr(true)} />
      ) : src && !err ? (
        <span className="leading-none" style={{ fontSize: Math.round(size * 0.5) }}>{src}</span>
      ) : (
        fallback
      )}
    </div>
  );
}
