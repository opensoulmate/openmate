import { NextRequest, NextResponse } from 'next/server';

/**
 * POST /api/smart-prompt/generate — 薄代理，唯一生成方=SoulMate。
 * 转发 opensoul /api/smart-prompt/generate（SoulMate agent 跑一轮）。
 * 红线：不旁路直连 LLM、不藏 key、无正则兜底——失败就报失败。
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.text();
    const auth = request.headers.get('authorization') || '';
    const res = await fetch('http://127.0.0.1:8090/api/smart-prompt/generate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(auth ? { Authorization: auth } : {}),
      },
      body,
      signal: AbortSignal.timeout(130000),
    });
    const data = await res.json().catch(() => ({ error: '生成服务返回异常' }));
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: '生成服务不可用' }, { status: 502 });
  }
}
