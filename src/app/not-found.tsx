import Link from "next/link";

export default function RootNotFound() {
  return (
    <div className="flex h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-md text-center">
        <div className="text-[120px] font-bold text-muted-foreground/10 leading-none select-none">404</div>
        <h2 className="text-xl font-bold text-foreground">页面未找到</h2>
        <p className="mt-2 text-sm text-muted-foreground">你访问的页面不存在或已被移动。</p>
        <div className="mt-8">
          <Link
            href="/chat"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2.5 text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            返回对话
          </Link>
        </div>
      </div>
    </div>
  );
}
