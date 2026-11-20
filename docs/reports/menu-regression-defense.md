# 菜单回归防线 · 落地报告

> 2026-11-20 · 承接两次菜单连环事故（520d2bca 重排丢菜单 → 2ea547ef 恢复），
> 两次都是"自报验证通过但用户眼前缺功能"。防线一句话：**谁碰菜单，比对不过就不许上**。

## 一、防线构成

| 组件 | 位置 | 作用 |
|---|---|---|
| 渲染 DOM 清点脚本 | `scripts/menu_dom_audit.py` | 真浏览器登录 → 抓顶栏/底栏/左栏/用户弹出菜单**渲染出的**全部菜单项 + 结构坐标 |
| 一键跑 | `npm run verify:menu` / `scripts/menu-regression.sh` | 自包含（自动找带 playwright 的 python + 系统 chromium 兜底），绿=0 / 红=1 |
| JSON 基线 | `docs/reports/menu-baseline/menu-baseline.json` | 当前正确态逐项清单 + 结构坐标 + AUDIT_55 覆盖清单 |
| 截图基线 | `docs/reports/menu-baseline/shots/` | 顶/底/左栏展开态 + 用户弹出菜单 + 移动态（人工复核用） |
| 说明书 | README「菜单回归防线（改菜单必跑）」+ openmate-opensoul-dev 技能纪律 | 改菜单必跑，不绿不许合入 |

## 二、基线（当前正确态，e2e_spot_1120 / role=user / 语言钉 zh）

- **顶栏 9 直挂**：Prometheus指标 / 通知中心 / 仪表盘 / 下载管理 / 进化 / 插件 / 系统总览 / ⌘K 搜索 / 设置（logo home 链接与健康件计 nonMenu，变化仅警告）
- **底栏直挂 31 件**（user 角色；含监控 `/monitoring`、器官 `/body-map` 单一 L1 入口、插件一级位 商机管理）——menu-audit 55 条基线中的 32 条直挂项除 3 条 adminOnly 外全部直挂，23 器官按用户正解收进左栏二级
- **左栏 organ-nav 39 件 / 7 分节**：核心 8 + 底座 8 + 高级 9 = **25 器官一个不少** + 工具 11 + 插件分节 3（商机管理/流程运行台/进化回流，安装长出/卸载收走）
- **用户弹出菜单 3 件**（查看资料/设置/退出登录）
- **覆盖 55 清单**：menu-audit 55 条底栏基线逐条落位核对（32 直挂 + 23 器官经左栏），一条不少；adminOnly 3 条（Soma管理/系统管理/权限管理）user 角色跳过并注明

## 三、判据（任一即红，打印逐项 diff）

缺失（基线有、渲染无）/ 降级（标签变化、副签变化）/ 多余（渲染有、基线无）/
顺序漂移 / 位置偏移（>10px）/ 尺寸差异（>6px）/ 数量级差异 / 分区整节缺失 /
角色不一致（菜单按角色过滤，须同角色比对）。

结构性比对（元素位置/数量级，容差内不报），**非像素级容噪**——排版微抖不误报，
重排丢件必报。

## 四、自证有效（绿→红→绿 三跑实录）

1. **绿**（基线生成后）：`bash scripts/menu-regression.sh` → `GREEN ✓ 与基线逐项一致`，exit 0。
2. **红**（故意删一个菜单项：`src/components/top-bar.tsx` 删掉 `/download` 顶栏入口，`npm run build` 34s，build 看护自动重启）→ exit 1，逐项 diff 首行即点名：
   `FAIL [顶栏] 缺失: top:/download (下载管理) — 基线有、渲染无`，
   另报 移动/顶栏缺失 + 数量级差异 9→8 件 + 后续项位置偏移级联，共 15 项差异。
3. **绿**（`git checkout -- src/components/top-bar.tsx` 恢复，重 build 18s）→ `GREEN ✓`，exit 0。

## 五、已知边界

- e2e 账号是 user 角色：3 个 adminOnly 入口不渲染，覆盖核对跳过（脚本 NOTE 注明）；
  要全量核对用 admin 账号：`MENU_AUDIT_USER=xxx MENU_AUDIT_PASSWORD=xxx npm run verify:menu`。
- 插件菜单区（L1 一级位 + 左栏插件分节）随插件安装状态变化：有意装/卸插件后需
  `--update-baseline` 重建基线并复核 diff。
- 改菜单有意变更 → `--update-baseline` 重建基线是**显式动作**，重建前后 diff 由人复核。
