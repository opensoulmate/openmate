#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
菜单回归防线 · 渲染 DOM 清点 + 结构性截图基线比对
=================================================

背景（2026-11-20 两次菜单连环事故）：520d2bca 重排丢菜单、2ea547ef 恢复。
两次都是"自报验证通过但用户眼前缺功能"。本脚本是回归防线：

  真浏览器登录 → 抓顶栏/底栏/左栏渲染出的全部菜单项 → 与 JSON 基线逐项 diff，
  缺失 / 降级（标签变化、层级迁移）/ 多余 / 顺序漂移 → 非零退出并打印逐项差异。

基线 = 恢复后的正确状态（docs/reports/menu-baseline/menu-baseline.json）：
  顶栏 9 直挂（指标/通知/仪表盘/下载/进化/插件/系统/⌘K/设置）
  底栏直挂全量（含监控、器官为单一 L1 入口、插件一级位）
  左栏 organ-nav 25 器官 + 11 工具 + 插件分节（**仅器官上下文**，见下）
  另附 AUDIT_55 覆盖清单（menu-audit 55 条底栏基线逐条落位核对，一条不少）

左栏策略口径（2026-11-20 用户令："顶部菜单和底部菜单为一级菜单，左侧为二级菜单"
——左侧=**当前一级的**二级清单，不是全局常驻器官表）：
  ① organ25：器官二级菜单（25 器官全集）只在「器官」一级入口（/body-map）及器官详情页；
  ② own-nav / plugin-l2：其他页面左侧=各自的二级子菜单（页面自注册 nav、插件二级、
     域 join 条目如 流程运行台→工作流、进化回流→进化）；
  ③ leaf：叶子页（如 会话管理/开销）不显示侧栏/紧凑态；
  ④ 铁律：非器官上下文的侧栏里出现 25 器官 = FAIL（"绝不能见谁都弹 25 器官"）。

用法：
  python3 scripts/menu_dom_audit.py                  # 比对基线（绿=0 / 红=1）
  python3 scripts/menu_dom_audit.py --update-baseline # 重建基线（改菜单后显式重建+人工复核 diff）
  python3 scripts/menu_dom_audit.py --base-url http://localhost:3000

铁律：脚本自包含可重复跑、失败给逐项 diff、语言钉死 zh、结构比对非像素容噪。
账号：默认 e2e_spot_1120（env MENU_AUDIT_USER / MENU_AUDIT_PASSWORD 可覆盖）。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

# ── 常量 ──────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://localhost:3000"
DEFAULT_USER = "e2e_spot_1120"
DEFAULT_PASSWORD = "SpotVerify2026"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASELINE = os.path.join(REPO_ROOT, "docs", "reports", "menu-baseline", "menu-baseline.json")

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}

# 结构性比对容差（非像素级容噪）：位置/尺寸允许的抖动，超过即报"位置偏移"
POS_TOL_PX = 10.0
SIZE_TOL_PX = 6.0
ZONE_TOL_PX = 24.0

# menu-audit 55 条底栏基线（520d2bca 前的正确态，来源 docs/reports/menu-restore2-report.md 逐项对照表）。
# home=落位区（bottombar=底栏直挂 / sidebar=左栏器官二级）；admin=True 仅 admin 角色渲染。
# 覆盖核对：每条必须在 home 区渲染出来——"55 一个不少"以此钉死。
AUDIT_55 = [
    {"href": "/chat", "label": "会话", "home": "bottombar"},
    {"href": "/ai-groups", "label": "AI 群组", "home": "bottombar"},
    {"href": "/knowledge", "label": "知识库", "home": "bottombar"},
    {"href": "/learn", "label": "学习", "home": "bottombar"},
    {"href": "/graph", "label": "知识图谱", "home": "bottombar"},
    {"href": "/knowledge-requests", "label": "知识库共享", "home": "bottombar"},
    {"href": "/cron", "label": "定时任务", "home": "bottombar"},
    {"href": "/workflow", "label": "工作流", "home": "bottombar"},
    {"href": "/skills", "label": "技能市场", "home": "bottombar"},
    {"href": "/mcp", "label": "MCP客户端", "home": "bottombar"},
    {"href": "/workspace", "label": "工作区", "home": "bottombar"},
    {"href": "/capture", "label": "采集", "home": "bottombar"},
    {"href": "/tags", "label": "标签管理", "home": "bottombar"},
    {"href": "/body-map", "label": "器官", "home": "bottombar"},
    {"href": "/discovery", "label": "发现引擎", "home": "bottombar"},
    {"href": "/soma-admin", "label": "Soma管理", "home": "bottombar", "admin": True},
    {"href": "/admin", "label": "系统管理", "home": "bottombar", "admin": True},
    {"href": "/permission", "label": "权限管理", "home": "bottombar", "admin": True},
    {"href": "/enterprise", "label": "企业管理", "home": "bottombar"},
    {"href": "/sessions", "label": "会话管理", "home": "bottombar"},
    {"href": "/spending", "label": "开销", "home": "bottombar"},
    {"href": "/intelligence", "label": "智能分析", "home": "bottombar"},
    {"href": "/ai-engine", "label": "AI 引擎", "home": "bottombar"},
    {"href": "/healer", "label": "自愈系统", "home": "bottombar"},
    {"href": "/topology", "label": "系统拓扑", "home": "bottombar"},
    {"href": "/registry", "label": "组件注册表", "home": "bottombar"},
    {"href": "/trajectory", "label": "轨迹", "home": "bottombar"},
    {"href": "/timeline", "label": "时间线", "home": "bottombar"},
    {"href": "/changelog", "label": "版本日志", "home": "bottombar"},
    {"href": "/docs", "label": "文档中心", "home": "bottombar"},
    {"href": "/devlog", "label": "开发记录", "home": "bottombar"},
    {"href": "/marketplace", "label": "市场", "home": "bottombar"},
    # ── 23 器官：底栏「器官」单一 L1 入口 → 左栏 organ-nav 二级 ──
    {"href": "/soma", "label": "躯体", "home": "sidebar"},
    {"href": "/cortex", "label": "皮层", "home": "sidebar"},
    {"href": "/vein", "label": "血管", "home": "sidebar"},
    {"href": "/gene", "label": "基因", "home": "sidebar"},
    {"href": "/vital", "label": "体征", "home": "sidebar"},
    {"href": "/gland", "label": "腺体", "home": "sidebar"},
    {"href": "/hippo", "label": "海马体", "home": "sidebar"},
    {"href": "/reflex", "label": "反射", "home": "sidebar"},
    {"href": "/heredity", "label": "遗传链", "home": "sidebar"},
    {"href": "/pulse", "label": "脉搏", "home": "sidebar"},
    {"href": "/nerve", "label": "神经", "home": "sidebar"},
    {"href": "/sense", "label": "感官", "home": "sidebar"},
    {"href": "/immune", "label": "免疫", "home": "sidebar"},
    {"href": "/marrow", "label": "骨髓", "home": "sidebar"},
    {"href": "/echo", "label": "回声", "home": "sidebar"},
    {"href": "/mirror", "label": "镜像", "home": "sidebar"},
    {"href": "/link", "label": "突触", "home": "sidebar"},
    {"href": "/nest", "label": "巢穴", "home": "sidebar"},
    {"href": "/limb", "label": "四肢", "home": "sidebar"},
    {"href": "/voice", "label": "声带", "home": "sidebar"},
    {"href": "/vision", "label": "视觉", "home": "sidebar"},
    {"href": "/mind", "label": "心智", "home": "sidebar"},
    {"href": "/soul", "label": "Soul", "home": "sidebar"},
]

# ── 左栏策略（SIDEBAR_POLICY）────────────────────────────────────────────
# expect: organ25=器官二级（25 全集）/ own-nav=页面自注册二级或紧凑导航 /
#         plugin-l2=插件二级 / domain+join=域内页+插件 join 二级条目 / leaf=无侧栏。
#         must_show=侧栏或页面必须出现的文案。
ORGAN_25 = [
    "/soul", "/cortex", "/nerve", "/vein", "/soma", "/sense", "/will", "/mate",
    "/immune", "/vital", "/marrow", "/gland", "/gene", "/echo", "/mirror", "/link",
    "/hippo", "/reflex", "/heredity", "/nest", "/pulse", "/limb", "/voice", "/vision", "/mind",
]

SIDEBAR_POLICY = [
    {"route": "/body-map", "expect": "organ25"},
    {"route": "/soul", "expect": "organ25"},
    {"route": "/hippo", "expect": "organ25"},
    {"route": "/pulse", "expect": "organ25"},
    {"route": "/will", "expect": "organ25"},
    {"route": "/download", "expect": "own-nav", "must_show": ["全部任务"]},
    {"route": "/monitoring", "expect": "own-nav", "must_show": ["概览"]},
    {"route": "/learn", "expect": "own-nav", "must_show": ["生成新课程"]},
    {"route": "/knowledge", "expect": "own-nav", "must_show": ["共享请求"]},
    {"route": "/workflow", "expect": "own-nav+join", "must_show": ["流程运行台"]},
    {"route": "/evolution", "expect": "own-nav+join", "must_show": ["进化回流"]},
    {"route": "/plugins/business-opportunity/home", "expect": "plugin-l2", "must_show": ["商机发现"]},
    {"route": "/sessions", "expect": "leaf"},
    {"route": "/spending", "expect": "leaf"},
    {"route": "/topology", "expect": "leaf"},
    {"route": "/timeline", "expect": "domain+join", "must_show": ["进化回流"]},
    {"route": "/changelog", "expect": "domain+join", "must_show": ["进化回流"]},
    {"route": "/admin", "expect": "leaf"},
    {"route": "/marketplace", "expect": "leaf"},
]

# 侧栏 shell 内清点（shadcn Sidebar 根；hrefs=侧栏全部链接；text=侧栏文案）
POLICY_EXTRACT_JS = r"""
() => {
  const sb = document.querySelector('[data-slot="sidebar"], [data-sidebar="sidebar"]');
  const hrefs = [];
  if (sb) for (const a of sb.querySelectorAll('a[href]')) hrefs.push(a.getAttribute('href'));
  return {
    shell: !!sb,
    hrefs,
    shellText: sb ? (sb.innerText || '') : '',
    bodyText: (document.body.innerText || '').slice(0, 4000),
    path: location.pathname,
  };
}
"""

# ── 浏览器内提取（单次执行，库存 + 结构坐标一网打尽）────────────────────────

EXTRACT_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  };
  const scrollAdj = (el, rect) => {
    // 底栏横向滚动容器内的条目：x 换算到内容坐标，滚动不造成假位置偏移
    let n = el.parentElement;
    while (n && n !== document.body) {
      if (n.scrollWidth > n.clientWidth + 4) {
        const cr = n.getBoundingClientRect();
        return { x: rect.x - cr.x + n.scrollLeft, y: rect.y - cr.y, w: rect.w, h: rect.h };
      }
      n = n.parentElement;
    }
    return rect;
  };
  const spansOf = (el) => Array.from(el.querySelectorAll('span'))
    .map(s => norm(s.innerText)).filter(t => t && !/^\d+\+?$/.test(t) && t !== '99+');
  const itemOf = (el, keyPrefix) => {
    const href = el.getAttribute('href');
    const title = el.getAttribute('title');
    const spans = spansOf(el);
    const label = title || spans[0] || norm(el.innerText);
    const tag = (!title && spans.length > 1) ? spans.slice(1).join(' ') : '';
    const rect = rectOf(el);
    return {
      key: keyPrefix + (href || ('btn:' + (title || label))),
      href: href || null,
      label: norm(label),
      tag: norm(tag),
      kbd: !!el.querySelector('kbd'),
      rect: scrollAdj(el, rect),
      visible: rect.w > 0 && rect.h > 0,
    };
  };

  // ── 顶栏：.om-chrome 内 a/button 全量（logo/健康件标 nonMenu）──
  const topRoot = document.querySelector('.om-chrome');
  const topbar = [];
  if (topRoot) {
    for (const el of topRoot.querySelectorAll('a,button')) {
      const it = itemOf(el, 'top:');
      // 品牌 home 链接（logo→/chat）与健康件不算菜单项；9 直挂=7 链接+⌘K+设置
      const isLogo = el.tagName === 'A' && el.getAttribute('href') === '/chat' && !!el.querySelector('img');
      const isPlainBtn = el.tagName === 'BUTTON' && !el.querySelector('kbd') && !el.getAttribute('href');
      it.menu = !(isLogo || isPlainBtn);
      topbar.push(it);
    }
  }

  // ── 底栏：<nav> 内 a/button 全量 ──
  let botRoot = null;
  for (const n of document.querySelectorAll('nav')) {
    if (n.className.includes('z-20') && n.className.includes('border-t')) { botRoot = n; break; }
    if (!botRoot || n.querySelectorAll('a[href]').length > botRoot.querySelectorAll('a[href]').length) botRoot = n;
  }
  const bottombar = [];
  if (botRoot) {
    for (const el of botRoot.querySelectorAll('a,button')) {
      const it = itemOf(el, 'bot:');
      it.menu = !!el.getAttribute('href');
      bottombar.push(it);
    }
  }

  // ── 左栏 organ-nav：从 /soul 锚点上溯到分节容器，按分节清点 ──
  const sidebar = { sections: [], rootRect: null };
  const soul = document.querySelector('a[href="/soul"]');
  if (soul) {
    let root = soul.parentElement;
    for (let i = 0; i < 10 && root; i++) {
      const as = root.querySelectorAll('a[href]');
      if (as.length >= 30 && root.querySelector('a[href="/mind"]') && root.querySelector('a[href="/learn"]')) break;
      root = root.parentElement;
    }
    if (root) {
      sidebar.rootRect = rectOf(root);
      for (const sec of root.children) {
        if (!sec.querySelector) continue;
        const links = Array.from(sec.querySelectorAll('a[href]'));
        if (links.length === 0) continue;
        const titleEl = sec.querySelector('div');
        const title = titleEl ? norm(titleEl.innerText) : '';
        sidebar.sections.push({
          title,
          items: links.map(el => {
            const it = itemOf(el, 'side:');
            it.menu = true;
            return it;
          }),
        });
      }
    }
  }

  // ── 用户弹出菜单（点开头像后）──
  const usermenu = [];
  const pop = document.querySelector('div[class*="z-[100]"]');
  if (pop) {
    for (const b of pop.querySelectorAll('button')) {
      const t = norm(b.innerText);
      if (!t) continue;
      usermenu.push({ key: 'popup:' + t, href: null, label: t, tag: '', kbd: false, rect: rectOf(b), visible: true, menu: true });
    }
  }

  return {
    topbar, bottombar, sidebar, usermenu,
    url: location.pathname,
    lang: localStorage.getItem('openmate-language') || '',
  };
}
"""

# ── 工具函数 ──────────────────────────────────────────────────────────────


def log(msg):
    print(msg, file=sys.stderr)


def die(msg, code=2):
    print("FATAL: " + msg, file=sys.stderr)
    sys.exit(code)


def find_chromium():
    env = os.environ.get("CHROME_PATH")
    if env and os.path.exists(env):
        return env
    for c in ("/usr/sbin/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"):
        if os.path.exists(c):
            return c
    return None  # playwright 自带浏览器


def launch_browser(pw):
    exe = find_chromium()
    kw = {"headless": True}
    if exe:
        kw["executable_path"] = exe
    else:
        log("提示: 未找到系统 chromium，尝试 Playwright 自带浏览器（需 playwright install chromium）")
    return pw.chromium.launch(**kw)


def wait_server(base_url, timeout_s=20):
    import urllib.request
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(base_url + "/login", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def do_login(pg, base_url, user, password):
    pg.goto(base_url + "/login", wait_until="domcontentloaded")
    pg.wait_for_selector('input[type="password"]', timeout=30000)
    pg.fill('input[type="password"]', password)
    pg.fill('input:not([type="password"])', user)
    pg.click('button:has-text("登录"), button:has-text("Sign In")')
    # 登录成功：window.location.href 全页跳转离开 /login
    for _ in range(60):
        pg.wait_for_timeout(500)
        if "/login" not in pg.url:
            break
        if pg.query_selector('input[placeholder*="验证码"], input[placeholder*="code" i]'):
            die("账号进入邮箱验证码环节（403 未验证/2FA），无法自动登录——请先人工验证账号")
    else:
        err = pg.evaluate("() => (document.body.innerText || '').slice(0, 300)")
        die("登录未跳转离开 /login，页面状态：" + err)
    tok = pg.evaluate("() => localStorage.getItem('openmate-token') || ''")
    if not tok:
        die("登录后 localStorage 无 openmate-token")
    role = "user"
    try:
        import base64
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        role = json.loads(base64.urlsafe_b64decode(payload)).get("role", "user")
    except Exception:
        pass
    return role


def extract(pg):
    return pg.evaluate(EXTRACT_JS)


def capture_state(pg, base_url, shots_dir, tag, with_usermenu=True, need_sidebar=True):
    """清点 + 截图 + 结构坐标，一份数据三用。"""
    pg.goto(base_url + "/body-map", wait_until="domcontentloaded")
    if need_sidebar:
        pg.wait_for_selector('a[href="/soul"]', timeout=30000)
    else:
        pg.wait_for_selector('nav a[href="/chat"]', timeout=30000)
    pg.wait_for_timeout(2500)  # 插件菜单 fetch + 健康点位稳定

    snap = extract(pg)

    if with_usermenu:
        # 点开头像弹出菜单 → 清点 → 截图 → 关闭
        avatar = pg.query_selector('nav button')
        if avatar:
            avatar.click()
            pg.wait_for_timeout(600)
            snap = extract(pg)
            pop = pg.query_selector('div[class*="z-[100]"]')
            if pop and shots_dir:
                try:
                    pop.screenshot(path=os.path.join(shots_dir, f"{tag}-usermenu.png"))
                except Exception:
                    pass
            pg.keyboard.press("Escape")
            pg.mouse.click(720, 400)
            pg.wait_for_timeout(400)

    if shots_dir:
        os.makedirs(shots_dir, exist_ok=True)
        pg.screenshot(path=os.path.join(shots_dir, f"{tag}-full.png"))
        for zone, sel in (("topbar", ".om-chrome"), ("bottombar", "nav:last-of-type")):
            el = pg.query_selector(sel)
            if el:
                try:
                    el.screenshot(path=os.path.join(shots_dir, f"{tag}-{zone}.png"))
                except Exception:
                    pass
        # 左栏展开态：按 sidebar.rootRect 裁剪（结构区域，非整页）
        sr = snap.get("sidebar", {}).get("rootRect")
        if sr:
            try:
                pg.screenshot(path=os.path.join(shots_dir, f"{tag}-sidebar.png"), clip={
                    "x": max(0, sr["x"] - 12), "y": max(0, sr["y"] - 12),
                    "width": sr["w"] + 24, "height": sr["h"] + 24,
                })
            except Exception:
                pass
    return snap


# ── diff 引擎 ─────────────────────────────────────────────────────────────


def item_map(items):
    m = {}
    for it in items:
        m.setdefault(it["key"], it)
    return m


def key_order(items):
    return [it["key"] for it in items]


def diff_item_lists(zone, base_items, live_items, strict_only=True):
    """逐项 diff：缺失 / 多余 / 标签降级 / 顺序差异。返回 (fails, warns)。"""
    fails, warns = [], []
    bm, lm = item_map(base_items), item_map(live_items)
    bkeys = [k for k in key_order(base_items) if not strict_only or bm[k].get("menu", True)]
    lkeys = [k for k in key_order(live_items) if not strict_only or lm[k].get("menu", True)]

    for k in bkeys:
        if k not in lm:
            it = bm[k]
            fails.append(f"[{zone}] 缺失: {k} ({it.get('label','')}) — 基线有、渲染无")
            continue
        bl, ll = bm[k].get("label", ""), lm[k].get("label", "")
        if bl and ll and bl != ll:
            fails.append(f"[{zone}] 降级(标签变化): {k} 基线「{bl}」→ 渲染「{ll}」")
        bt, lt = bm[k].get("tag", ""), lm[k].get("tag", "")
        if bt and lt and bt != lt:
            fails.append(f"[{zone}] 降级(标签变化): {k} 副签「{bt}」→「{lt}」")
    for k in lkeys:
        if k not in bm:
            it = lm[k]
            fails.append(f"[{zone}] 多余: {k} ({it.get('label','')}) — 渲染有、基线无")

    bcommon = [k for k in bkeys if k in lm]
    lcommon = [k for k in lkeys if k in bm]
    if bcommon != lcommon:
        # 找到第一处顺序分歧逐项打印
        for i, (a, b) in enumerate(zip(bcommon, lcommon)):
            if a != b:
                fails.append(f"[{zone}] 顺序差异: 第{i+1}位 基线「{a}」→ 渲染「{b}」（后续同理）")
                break

    # nonMenu 件（logo/健康件/头像/终端钮）：变化只警告
    for k in key_order(base_items):
        if strict_only and bm[k].get("menu", True):
            continue
        if k not in lm:
            warns.append(f"[{zone}] nonMenu 件消失: {k} ({bm[k].get('label','')})")
    for k in key_order(live_items):
        if strict_only and lm[k].get("menu", True):
            continue
        if k not in bm:
            warns.append(f"[{zone}] nonMenu 件新增: {k} ({lm[k].get('label','')})")
    return fails, warns


def diff_sidebar(base_side, live_side):
    fails, warns = [], []
    bsec = {s["title"]: s for s in base_side.get("sections", [])}
    lsec = {s["title"]: s for s in live_side.get("sections", [])}
    for title, s in bsec.items():
        if title not in lsec:
            fails.append(f"[左栏] 缺失(分区): 「{title}」整节未渲染")
            continue
        f2, w2 = diff_item_lists(f"左栏/{title}", s["items"], lsec[title]["items"])
        fails += f2
        warns += w2
    for title in lsec:
        if title not in bsec:
            n = len(lsec[title]["items"])
            fails.append(f"[左栏] 多余(分区): 「{title}」({n} 项) — 渲染有、基线无")
    # 分区顺序
    bt = [s["title"] for s in base_side.get("sections", []) if s["title"] in lsec]
    lt = [s["title"] for s in live_side.get("sections", []) if s["title"] in bsec]
    if bt != lt:
        fails.append(f"[左栏] 顺序差异(分区): 基线 {bt} → 渲染 {lt}")
    return fails, warns


def diff_layout(zone, base_layout, live_layout):
    """结构性比对：元素位置/数量级差异（容差内不报，非像素级容噪）。"""
    fails = []
    bi = {it["key"]: it["rect"] for it in base_layout.get("items", [])}
    li = {it["key"]: it["rect"] for it in live_layout.get("items", [])}
    if len(bi) != len(li):
        fails.append(f"[{zone}] 数量级差异: 基线 {len(bi)} 件 → 渲染 {len(li)} 件")
    for k, br in bi.items():
        lr = li.get(k)
        if not lr:
            continue  # 缺失已由库存 diff 报
        dx, dy = abs(br["x"] - lr["x"]), abs(br["y"] - lr["y"])
        dw, dh = abs(br["w"] - lr["w"]), abs(br["h"] - lr["h"])
        if dx > POS_TOL_PX or dy > POS_TOL_PX:
            fails.append(f"[{zone}] 位置偏移: {k} 基线({br['x']:.0f},{br['y']:.0f}) → 渲染({lr['x']:.0f},{lr['y']:.0f})")
        if dw > SIZE_TOL_PX or dh > SIZE_TOL_PX:
            fails.append(f"[{zone}] 尺寸差异: {k} 基线({br['w']:.0f}x{br['h']:.0f}) → 渲染({lr['w']:.0f}x{lr['h']:.0f})")
    return fails


def layout_zone(items):
    return {"items": [{"key": it["key"], "rect": it["rect"]} for it in items if it.get("menu", True)]}


def check_coverage(live, role):
    """AUDIT_55 覆盖清单：55 条一条不少（adminOnly 按角色裁剪并注明）。"""
    fails, notes = [], []
    bot = set(it["href"] for it in live["desktop"]["bottombar"] if it.get("href"))
    side = set()
    for s in live["desktop"]["sidebar"].get("sections", []):
        side |= set(it["href"] for it in s["items"] if it.get("href"))
    pool = {"bottombar": bot, "sidebar": side}
    skipped = 0
    for e in AUDIT_55:
        if e.get("admin") and role != "admin":
            skipped += 1
            continue
        if e["href"] not in pool[e["home"]]:
            fails.append(f"[覆盖55] 缺失: {e['href']} ({e['label']}) 应在 {e['home']} 渲染")
    if skipped:
        notes.append(f"[覆盖55] adminOnly {skipped} 条本次角色(role={role})不渲染，已跳过（admin 角色跑一次可全量核对）")
    return fails, notes, len(AUDIT_55) - skipped


def check_sidebar_policy(pg, base_url):
    """左栏策略清点（SIDEBAR_POLICY 口径）：逐路由真打开、等稳定、逐项判定。"""
    fails, rows = [], []
    for rule in SIDEBAR_POLICY:
        route, expect = rule["route"], rule["expect"]
        pg.goto(base_url + route, wait_until="domcontentloaded")
        state = pg.evaluate(POLICY_EXTRACT_JS)
        for _ in range(8):  # 等水合稳定：连续两次读数一致即停
            pg.wait_for_timeout(1500)
            cur = pg.evaluate(POLICY_EXTRACT_JS)
            stable = cur == state
            state = cur
            if stable:
                break
        hrefs = state.get("hrefs", [])
        organ_here = [h for h in hrefs if h in ORGAN_25]
        missing = [h for h in ORGAN_25 if h not in hrefs]
        row = {"route": route, "expect": expect, "shell": state["shell"],
               "links": len(hrefs), "organ": len(organ_here)}
        # 铁律：非器官上下文绝不弹 25 器官
        if expect == "organ25":
            if not state["shell"]:
                fails.append(f"[左栏策略] {route}: 应为器官二级（organ25）但侧栏 shell 未渲染")
            elif missing:
                row["missing"] = missing
                fails.append(f"[左栏策略] {route}: 器官二级缺 {len(missing)} 个（一个不少要求）: {missing}")
        else:
            if organ_here:
                fails.append(f"[左栏策略] {route}: 非器官上下文弹出器官表（{len(organ_here)} 个器官链接）—— 违反'绝不能见谁都弹 25 器官'")
        for txt in rule.get("must_show", []):
            if txt not in state["shellText"] and txt not in state["bodyText"]:
                fails.append(f"[左栏策略] {route}: 期望的二级条目「{txt}」未出现")
        rows.append(row)
        log(f"  左栏策略 {route:38s} expect={expect:14s} shell={'Y' if state['shell'] else 'N'} links={len(hrefs):3d} organ={len(organ_here)}")
    return fails, rows


# ── 主流程 ────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="菜单回归防线：渲染 DOM 清点 + 结构比对")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--update-baseline", action="store_true", help="重建基线（显式确认菜单变更后使用）")
    ap.add_argument("--shots-dir", default=None, help="截图输出目录（默认 基线同级 shots/ 或 last-run/）")
    ap.add_argument("--report-json", default=None, help="逐项 diff 报告 JSON 输出路径")
    ap.add_argument("--user", default=os.environ.get("MENU_AUDIT_USER", DEFAULT_USER))
    ap.add_argument("--password", default=os.environ.get("MENU_AUDIT_PASSWORD", DEFAULT_PASSWORD))
    args = ap.parse_args()

    base_dir = os.path.dirname(os.path.abspath(args.baseline))
    if args.shots_dir:
        shots_dir = args.shots_dir
    else:
        shots_dir = os.path.join(base_dir, "shots" if args.update_baseline else "last-run")

    if not wait_server(args.base_url):
        die(f"目标服务不可达: {args.base_url}（先启动 OpenMate Web，如 systemctl --user start openmate-web）")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("缺 playwright（pip install playwright）；或换用带 playwright 的解释器")

    with sync_playwright() as pw:
        browser = launch_browser(pw)

        # ── 桌面 1440×900 ──
        ctx = browser.new_context(viewport=DESKTOP, locale="zh-CN")
        ctx.add_init_script("try { localStorage.setItem('openmate-language','zh'); } catch(e) {}")
        pg = ctx.new_page()
        log(f"登录 {args.base_url} @ {args.user} …")
        role = do_login(pg, args.base_url, args.user, args.password)
        log(f"登录成功 role={role}")
        log("清点桌面渲染 DOM（/body-map 展开态）…")
        desktop = capture_state(pg, args.base_url, shots_dir, "desktop")
        log("左栏策略逐页清点（器官二级仅器官上下文 / 绝不见谁都弹 25 器官）…")
        policy_fails, policy_rows = check_sidebar_policy(pg, args.base_url)
        ctx.close()

        # ── 移动 390×844 ──
        ctx2 = browser.new_context(viewport=MOBILE, locale="zh-CN")
        ctx2.add_init_script("try { localStorage.setItem('openmate-language','zh'); } catch(e) {}")
        pg2 = ctx2.new_page()
        role2 = do_login(pg2, args.base_url, args.user, args.password)
        log("清点移动渲染 DOM …")
        mobile = capture_state(pg2, args.base_url, shots_dir, "mobile", with_usermenu=False, need_sidebar=False)
        ctx2.close()
        browser.close()

    live = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "base_url": args.base_url,
            "role": role,
            "language": "zh",
            "build_id": open(os.path.join(REPO_ROOT, ".next", "BUILD_ID")).read().strip()
            if os.path.exists(os.path.join(REPO_ROOT, ".next", "BUILD_ID")) else "",
        },
        "desktop": {
            "topbar": desktop["topbar"],
            "bottombar": desktop["bottombar"],
            "sidebar": desktop["sidebar"],
            "usermenu": desktop["usermenu"],
        },
        "mobile": {
            "topbar": mobile["topbar"],
            "bottombar": mobile["bottombar"],
        },
    }
    live["layout"] = {
        "desktop": {
            "topbar": layout_zone(desktop["topbar"]),
            "bottombar": layout_zone(desktop["bottombar"]),
            "sidebar": {"items": [it for s in desktop["sidebar"].get("sections", []) for it in layout_zone(s["items"])["items"]]},
        },
        "mobile": {
            "topbar": layout_zone(mobile["topbar"]),
            "bottombar": layout_zone(mobile["bottombar"]),
        },
    }
    live["sidebar_policy"] = {"rules": SIDEBAR_POLICY, "results": policy_rows}

    if args.update_baseline:
        out = dict(live)
        out["coverage"] = {"audit55": AUDIT_55, "sidebar_policy": SIDEBAR_POLICY}
        os.makedirs(base_dir, exist_ok=True)
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        n = lambda z: len([i for i in live["desktop"][z] if i.get("menu", True)])
        ns = sum(len(s["items"]) for s in live["desktop"]["sidebar"]["sections"])
        log(f"基线已重建: {args.baseline}")
        log(f"  顶栏 {n('topbar')} 件 / 底栏 {n('bottombar')} 件 / 左栏 {ns} 件 "
            f"({len(live['desktop']['sidebar']['sections'])} 分节) / 弹出菜单 {len(live['desktop']['usermenu'])} 件 / role={role}")
        log(f"  截图: {shots_dir}")
        print(json.dumps({"ok": True, "baseline": args.baseline, "role": role,
                          "counts": {"topbar": n("topbar"), "bottombar": n("bottombar"), "sidebar": ns}},
                         ensure_ascii=False))
        return

    # ── 比对 ──
    if not os.path.exists(args.baseline):
        die(f"基线不存在: {args.baseline}（先跑 --update-baseline 生成）")
    with open(args.baseline, encoding="utf-8") as f:
        base = json.load(f)

    fails, warns, notes = [], [], []

    b_role = base.get("meta", {}).get("role")
    if b_role and b_role != role:
        fails.append(f"[meta] 角色不一致: 基线 role={b_role} → 本次 role={role}（菜单按角色过滤，须同角色比对）")

    for zone, items in (("顶栏", "topbar"), ("底栏", "bottombar")):
        f2, w2 = diff_item_lists(zone, base["desktop"][items], live["desktop"][items])
        fails += f2
        warns += w2
    f2, w2 = diff_sidebar(base["desktop"]["sidebar"], live["desktop"]["sidebar"])
    fails += f2
    warns += w2
    f2, w2 = diff_item_lists("用户弹出菜单", base["desktop"].get("usermenu", []), live["desktop"].get("usermenu", []))
    fails += f2
    warns += w2

    # 移动端：key + 顺序（标签在小屏隐藏，不比文案）
    for zone, items in (("移动/顶栏", "topbar"), ("移动/底栏", "bottombar")):
        bk = key_order([i for i in base["mobile"][items] if i.get("menu", True)])
        lk = key_order([i for i in live["mobile"][items] if i.get("menu", True)])
        for k in bk:
            if k not in lk:
                fails.append(f"[{zone}] 缺失: {k}")
        for k in lk:
            if k not in bk:
                fails.append(f"[{zone}] 多余: {k}")
        if [k for k in bk if k in lk] != [k for k in lk if k in bk]:
            fails.append(f"[{zone}] 顺序差异")

    # 结构性比对（位置/数量级，容差内不报）
    for vp in ("desktop", "mobile"):
        for zone in base.get("layout", {}).get(vp, {}):
            fails += diff_layout(f"{vp}/{zone}",
                                 base["layout"][vp][zone],
                                 live["layout"].get(vp, {}).get(zone, {"items": []}))

    # AUDIT_55 覆盖
    f2, notes2, checked = check_coverage(live, role)
    fails += f2
    notes += notes2

    # 左栏策略（器官二级仅器官上下文 / 绝不见谁都弹 25 器官）
    fails += policy_fails

    # ── 输出 ──
    print("=" * 72)
    print("菜单回归比对 · " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 72)
    if notes:
        for x in notes:
            print("NOTE  " + x)
    for x in warns:
        print("WARN  " + x)
    for x in fails:
        print("FAIL  " + x)
    print("-" * 72)
    counts = {
        "顶栏": len([i for i in live["desktop"]["topbar"] if i.get("menu", True)]),
        "底栏": len([i for i in live["desktop"]["bottombar"] if i.get("menu", True)]),
        "左栏": sum(len(s["items"]) for s in live["desktop"]["sidebar"]["sections"]),
        "弹出菜单": len(live["desktop"]["usermenu"]),
    }
    print(f"渲染清点: 顶栏 {counts['顶栏']} / 底栏 {counts['底栏']} / 左栏 {counts['左栏']} "
          f"/ 弹出菜单 {counts['弹出菜单']} · 覆盖55: 核对 {checked} 条 · 左栏策略: {len(policy_rows)} 路由")
    if fails:
        print(f"结果: RED ✗ 共 {len(fails)} 项差异（缺失/降级/多余/顺序/位置）—— 不许上")
        sys.exit(1)
    print(f"结果: GREEN ✓ 与基线逐项一致（警告 {len(warns)} 条）")
    sys.exit(0)


if __name__ == "__main__":
    main()
