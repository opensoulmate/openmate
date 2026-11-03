// 客户端追踪信标：把关键入口的触发记录落盘，供排查"一次操作跑了两遍"类问题
import { NextRequest, NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const LOG = path.join(process.cwd(), 'logs', 'client-trace.log');

export async function POST(req: NextRequest) {
  try {
    const body = await req.text();
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    fs.appendFileSync(LOG, `${new Date().toISOString()} ${body}\n`);
  } catch { /* 追踪失败不影响业务 */ }
  return NextResponse.json({ ok: true });
}
