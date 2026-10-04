// user_message角色保真处理面真实测试（21:40轮遗留#4销账）
// 编译：tsc transpileModule（user-message.ts纯函数，无React依赖）→ require真实调用
// 覆盖：①buildUserMessage行为（角色/形状/id兜底）②与loadHistory映射产物同形
//       ③接线钉（chat-client user_message分支+import+不setLoading+loadHistory角色映射）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.user-msg-'));
// 临时产物自清理（统一纪律：失败早退也不留tmpdir垃圾）
process.on('exit', () => { try { fs.rmSync(outDir, { recursive: true, force: true }); } catch {} });

function compile(relSrc, relOut) {
  const src = fs.readFileSync(path.join(ROOT, relSrc), 'utf8');
  const { outputText } = ts.transpileModule(src, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: relSrc,
  });
  const outFile = path.join(outDir, relOut);
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, outputText);
  return outFile;
}

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (typeof request === 'string' && request.startsWith('@/')) {
    request = path.join(outDir, request.slice(2)) + '.js';
  }
  return origResolve.call(this, request, ...args);
};

const helperFile = compile('src/app/(app)/chat/user-message.ts', 'chat/user-message.js');
const { buildUserMessage } = require(helperFile);

const chatSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/chat/chat-client.tsx'), 'utf8');
const relaySrc = fs.readFileSync(path.join(ROOT, 'acp-proxy/agent/wakeup_relay.py'), 'utf8');

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

// ── ① buildUserMessage行为 ──
const m = buildUserMessage('⏰ [ScheduleWakeup 到点唤醒｜系统自动回合，非用户消息] 60秒后检查任务');
assert(m.role === 'user', '①role固定user（用户气泡）');
assert(Array.isArray(m.parts) && m.parts.length === 1, '①单text part');
assert(m.parts[0].type === 'text', '①part.type=text');
assert(m.parts[0].text.startsWith('⏰ [ScheduleWakeup'), '①text原文保真（唤醒触发行标记开头）');
assert(typeof m.id === 'string' && m.id.length > 0, '①id兜底=非空字符串');
assert(m.timestamp instanceof Date, '①timestamp=Date');

const m2 = buildUserMessage('继续', 'msg-123');
assert(m2.id === 'msg-123', '①显式id优先');
assert(m2.role === 'user' && m2.parts[0].text === '继续', '①显式id形状不变');

// ── ② 与loadHistory映射产物同形（键集逐字一致：live消息与历史消息同一形状）──
const keys = Object.keys(m).sort();
assert(JSON.stringify(keys) === JSON.stringify(['id', 'parts', 'role', 'timestamp']),
  '②键集={id,parts,role,timestamp}与loadHistory产物同形');
assert(JSON.stringify(Object.keys(m2).sort()) === JSON.stringify(keys), '②两种构造路径键集一致');

// ── ③ 接线钉（写了≠接线了）──
assert(chatSrc.includes("sessionUpdate === 'user_message'"), '③chat-client有user_message分支');
assert(chatSrc.includes("import { buildUserMessage } from './user-message'"), '③chat-client import单一真源');
assert(chatSrc.includes('buildUserMessage(text)'), '③分支调用buildUserMessage');
assert(chatSrc.includes("role: isAgent ? 'agent' : 'user'"), '③loadHistory role映射在场（一致性根据）');
const branchStart = chatSrc.indexOf("sessionUpdate === 'user_message'");
const branchEnd = chatSrc.indexOf('else if', branchStart);
const branch = chatSrc.slice(branchStart, branchEnd);
assert(!branch.includes('setLoading'), '③user_message分支不动loading（触发行先到agent仍在工作）');
assert(!branch.includes('incrementUnread'), '③user_message分支不计未读（非agent产出内容）');
assert(relaySrc.includes('"user_message"'), '③wakeup_relay按role推user_message');
assert(relaySrc.includes('str(row.get("role") or "").strip().lower() == "user"'), '③relay角色判定单一真源');

// 清理临时编译产物
try { fs.rmSync(outDir, { recursive: true, force: true }); } catch {}

console.log(failed === 0 ? '\nALL PASS' : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
