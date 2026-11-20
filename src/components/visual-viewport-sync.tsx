'use client';

import { useEffect } from 'react';

/**
 * 移动端虚拟键盘遮挡根修（用户报障：点输入框，键盘弹出把输入框顶没了）。
 *
 * 三层方案，任一可用即修：
 *   1. viewport meta interactive-widget=resizes-content（Chrome 108+，layout.tsx 挂）——
 *      键盘直接缩布局视口，flow 布局的输入栏自动上移；
 *   2. visualViewport 监听（iOS Safari / 旧 WebView 不认 meta 时的兜底）——
 *      键盘弹出时把 --app-height 钉到可视高度（px），应用壳高度跟着缩，输入栏露出键盘上方；
 *   3. --keyboard-inset 供 fixed 定位面板（如终端面板）让出键盘高度。
 *
 * 保险规则（桌面零回归）：
 *   - pinch-zoom（scale≠1）时 visualViewport.height 是缩放假象不是键盘，直接跳过；
 *   - 仅触屏（pointer:coarse）聚焦可编辑元素、或出现明显键盘内缩（>60px）时才接管高度，
 *     桌面端不写 var → 回退原有 100svh/100vh/100dvh 行为，一像素不变；
 *   - 键盘收起（blur / resize 回弹）即清 var，输入栏回位。
 */
export default function VisualViewportSync() {
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    if (!vv) return;

    const coarse = () => window.matchMedia('(pointer: coarse)').matches;

    // 可编辑面：textarea / select / 文本类 input / contentEditable（SmartPrompt 用 CodeMirror）
    const isEditable = (el: EventTarget | null): boolean => {
      if (!(el instanceof HTMLElement)) return false;
      if (el.isContentEditable) return true;
      const tag = el.tagName;
      if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
      if (tag === 'INPUT') {
        const type = ((el as HTMLInputElement).type || 'text').toLowerCase();
        return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file', 'image'].includes(type);
      }
      return false;
    };

    let editableFocused = isEditable(document.activeElement);

    const sync = () => {
      // pinch-zoom 中（scale≠1）visualViewport.height 是缩放假象，不是键盘，保持现状
      if (Math.abs(vv.scale - 1) > 0.02) return;

      const layoutH = window.innerHeight;
      const visualH = vv.height;
      const inset = Math.max(0, layoutH - visualH); // 键盘遮挡高度（布局视口未缩时 >0）
      const keyboardLikely = inset > 60 || (editableFocused && coarse());

      if (!keyboardLikely) {
        // 键盘收起：清覆盖，输入栏回原位（100svh/100vh/100dvh 原语义）
        root.style.removeProperty('--app-height');
        root.style.removeProperty('--keyboard-inset');
        root.style.removeProperty('--vv-offset');
        document.body.style.transform = '';
        return;
      }

      root.style.setProperty('--app-height', `${Math.round(visualH)}px`);
      root.style.setProperty('--keyboard-inset', `${Math.round(inset)}px`);

      // iOS 键盘弹出时可能平移可视视口（offsetTop>0）：body 整体下移补齐，
      // 输入栏正好贴键盘顶（fixed 子元素此时挂 body=可视区，反而更正确）；
      // 桌面/Android offsetTop 恒 0 → transform 清空，零副作用。
      const offset = Math.max(0, Math.round(vv.offsetTop));
      root.style.setProperty('--vv-offset', `${offset}px`);
      document.body.style.transform = offset > 0 ? `translateY(${offset}px)` : '';
    };

    const onFocusIn = (e: FocusEvent) => {
      if (isEditable(e.target)) {
        editableFocused = true;
        sync();
      }
    };
    // 键盘收起（安卓返回键收键盘）不一定触发 blur：resize 回弹时 keyboardLikely 自然转 false 清除
    const onFocusOut = () => {
      editableFocused = isEditable(document.activeElement);
      requestAnimationFrame(sync);
    };

    sync();
    vv.addEventListener('resize', sync);
    vv.addEventListener('scroll', sync);
    window.addEventListener('resize', sync);
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('focusout', onFocusOut);
    return () => {
      vv.removeEventListener('resize', sync);
      vv.removeEventListener('scroll', sync);
      window.removeEventListener('resize', sync);
      document.removeEventListener('focusin', onFocusIn);
      document.removeEventListener('focusout', onFocusOut);
    };
  }, []);

  return null;
}
