'use client';

import { useEffect, useRef, useCallback, useMemo } from 'react';
import { EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine, highlightActiveLineGutter, Decoration, DecorationSet, WidgetType, ViewPlugin, ViewUpdate } from '@codemirror/view';
import { history, historyKeymap, defaultKeymap, undo, redo } from '@codemirror/commands';
import { EditorState, StateField, Compartment, type Range } from '@codemirror/state';
import { syntaxHighlighting, defaultHighlightStyle, bracketMatching, LanguageDescription, HighlightStyle } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import { markdown, markdownLanguage } from '@codemirror/lang-markdown';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { json } from '@codemirror/lang-json';
import { sql } from '@codemirror/lang-sql';
import { yaml } from '@codemirror/lang-yaml';
import { xml } from '@codemirror/lang-xml';
import type { Extension } from '@codemirror/state';

// No placeholder extension needed — React overlay handles it

// ── @提及原子 chip（微信式）：命中 token 渲染为一个整体，光标跳过、退格一次整删 ──
class MentionChipWidget extends WidgetType {
  constructor(private readonly label: string) { super(); }
  eq(other: MentionChipWidget) { return other.label === this.label; }
  toDOM() {
    const span = document.createElement('span');
    span.className = 'cm-mention-chip';
    span.textContent = `@${this.label}`;
    return span;
  }
  ignoreEvent() { return true; }
}

// token 前需有边界（行首/空白/常见标点），避免把邮箱 a@b 当提及
const MENTION_TOKEN_RE = /(^|[\s(（[｛{，。,.、;；:：!！?？"'`~])@([\w\u4e00-\u9fff·\-]+)/g;

function mentionChipExtension(getMap: () => Map<string, string>) {
  const build = (view: EditorView): DecorationSet => {
    const map = getMap();
    const text = view.state.doc.toString();
    const decos: Range<Decoration>[] = [];
    MENTION_TOKEN_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = MENTION_TOKEN_RE.exec(text))) {
      const from = m.index + m[1].length; // '@' 起点
      const token = m[2];
      const label = map.get(token.toLowerCase());
      if (!label) continue;
      // 尾随空格并入 chip：退格一次整删（微信手感）
      const tokenEnd = from + 1 + token.length;
      const to = text[tokenEnd] === ' ' ? tokenEnd + 1 : tokenEnd;
      decos.push(Decoration.replace({ widget: new MentionChipWidget(label), atomic: true }).range(from, to));
    }
    return Decoration.set(decos, true);
  };
  const factory = (view: EditorView) => {
    const plugin = {
      decorations: build(view),
      update(u: ViewUpdate) {
        if (u.docChanged || u.selectionSet) plugin.decorations = build(u.view);
      },
    };
    return plugin;
  };
  return ViewPlugin.define(factory, { decorations: (p) => p.decorations });
}

// Code languages for markdown code blocks
const codeLanguages = [
  LanguageDescription.of({ name: 'javascript', load: () => import('@codemirror/lang-javascript').then(m => m.javascript()) }),
  LanguageDescription.of({ name: 'typescript', load: () => import('@codemirror/lang-javascript').then(m => m.javascript({ typescript: true })) }),
  LanguageDescription.of({ name: 'python', load: () => import('@codemirror/lang-python').then(m => m.python()) }),
  LanguageDescription.of({ name: 'html', load: () => import('@codemirror/lang-html').then(m => m.html()) }),
  LanguageDescription.of({ name: 'css', load: () => import('@codemirror/lang-css').then(m => m.css()) }),
  LanguageDescription.of({ name: 'json', load: () => import('@codemirror/lang-json').then(m => m.json()) }),
  LanguageDescription.of({ name: 'sql', load: () => import('@codemirror/lang-sql').then(m => m.sql()) }),
  LanguageDescription.of({ name: 'yaml', load: () => import('@codemirror/lang-yaml').then(m => m.yaml()) }),
  LanguageDescription.of({ name: 'xml', load: () => import('@codemirror/lang-xml').then(m => m.xml()) }),
];

// Markdown language (module-level, created once)
const markdownLang = markdown({ base: markdownLanguage, codeLanguages });

// Syntax highlight colors via CSS variables
const openmateHighlight = syntaxHighlighting(HighlightStyle.define([
  { tag: tags.keyword, color: 'hsl(var(--primary))' },
  { tag: tags.operator, color: 'hsl(var(--muted-foreground))' },
  { tag: tags.special(tags.variableName), color: 'hsl(300 60% 70%)' },
  { tag: tags.typeName, color: 'hsl(var(--primary) / 0.8)' },
  { tag: tags.atom, color: 'hsl(25 80% 65%)' },
  { tag: tags.number, color: 'hsl(25 80% 65%)' },
  { tag: tags.definition(tags.variableName), color: 'hsl(var(--primary))' },
  { tag: tags.string, color: 'hsl(150 60% 60%)' },
  { tag: tags.special(tags.string), color: 'hsl(280 60% 65%)' },
  { tag: tags.comment, color: 'hsl(var(--muted-foreground) / 0.6)', fontStyle: 'italic' },
  { tag: tags.variableName, color: 'hsl(var(--foreground))' },
  { tag: tags.tagName, color: 'hsl(var(--primary))' },
  { tag: tags.bracket, color: 'hsl(var(--muted-foreground))' },
  { tag: tags.meta, color: 'hsl(var(--muted-foreground))' },
  { tag: tags.link, color: 'hsl(var(--primary))', textDecoration: 'underline' },
  { tag: tags.heading, color: 'hsl(var(--primary))', fontWeight: 'bold' },
  { tag: tags.emphasis, fontStyle: 'italic' },
  { tag: tags.strong, fontWeight: 'bold' },
  { tag: tags.strikethrough, textDecoration: 'line-through' },
]));

interface CodeMirrorEditorProps {
  value: string;
  onChange: (value: string) => void;
  onKeyDown?: (e: KeyboardEvent, view: EditorView) => boolean;
  placeholder?: string;
  readOnly?: boolean;
  className?: string;
  /** @提及原子 chip 词表：id/label → 显示名（微信式整体删除+着色） */
  mentionMap?: Record<string, string>;
}

export function CodeMirrorEditor({
  value,
  onChange,
  onKeyDown,
  placeholder = '',
  readOnly = false,
  className = '',
  mentionMap,
}: CodeMirrorEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const mentionMapRef = useRef<Record<string, string>>({});
  mentionMapRef.current = mentionMap || {};
  const mentionExt = useMemo(() => mentionChipExtension(() => {
    const src = mentionMapRef.current;
    const m = new Map<string, string>();
    for (const k in src) {
      m.set(k.toLowerCase(), src[k]);
      m.set(String(src[k]).toLowerCase(), src[k]);
    }
    return m;
  }), []);
  const onKeyDownRef = useRef(onKeyDown);
  // Track whether the last doc change was from user typing (so we skip the value sync)
  const userEditingRef = useRef(false);

  onChangeRef.current = onChange;
  onKeyDownRef.current = onKeyDown;

  const theme = useMemo(() => EditorView.theme({
    '&': {
      height: 'auto',
      maxHeight: '12rem',
      fontSize: '14px',
    },
    '&.cm-focused': { outline: 'none' },
    '.cm-scroller': {
      padding: '0 14px 0 0',
      overflow: 'auto',
      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      lineHeight: '1.6',
    },
    '.cm-editor': { height: '100%', padding: 0 },
    '.cm-content': {
      color: 'hsl(var(--foreground))',
      caretColor: 'hsl(var(--foreground))',
      position: 'relative',
      overflowWrap: 'break-word',
      wordBreak: 'break-word',
    },
    '&.cm-focused .cm-cursor': { borderLeftColor: 'hsl(var(--foreground))' },
    '.cm-gutters': {
      backgroundColor: 'transparent',
      border: 'none',
      padding: '0 2px !important',
      color: 'hsl(var(--muted-foreground) / 0.5) !important',
    },
    '.cm-gutterElement': {
      padding: '0 4px 0 0 !important',
      lineHeight: '1.6 !important',
      fontSize: '14px !important',
    },
    '.cm-line': {
      padding: '0',
      margin: 0,
      lineHeight: '1.6 !important',
      fontSize: '14px !important',
      overflowWrap: 'break-word',
      wordBreak: 'break-word',
    },
    '.cm-activeLineGutter': { backgroundColor: 'hsl(var(--accent))', color: 'hsl(var(--foreground))' },
    '.cm-activeLine': { backgroundColor: 'hsl(var(--accent) / 0.3)' },
    '.cm-selectionBackground, ::selection': { backgroundColor: 'hsl(var(--primary) / 0.25) !important' },
    '&.cm-focused .cm-selectionBackground': { backgroundColor: 'hsl(var(--primary) / 0.3) !important' },
    '.cm-matchingBracket': { backgroundColor: 'hsl(var(--primary) / 0.2)', outline: '1px solid hsl(var(--primary) / 0.4)' },
    '.cm-foldGutter': { display: 'none !important' },
    '.cm-tooltip': { backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', color: 'hsl(var(--popover-foreground))' },
    '.cm-tooltip-autocomplete': { '& > ul > li[aria-selected]': { backgroundColor: 'hsl(var(--accent))' } },
    '.cm-panels': { backgroundColor: 'hsl(var(--card))', color: 'hsl(var(--foreground))' },
    '.cm-panel': { display: 'none !important' },
    '.cm-searchMatch': { backgroundColor: 'hsl(var(--primary) / 0.2)' },
    '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'hsl(var(--primary) / 0.35)' },
    '.cm-mention-chip': {
      color: 'hsl(var(--primary))',
      backgroundColor: 'hsl(var(--primary) / 0.12)',
      borderRadius: '4px',
      padding: '0 3px',
      margin: '0 1px',
      fontWeight: '500',
      whiteSpace: 'nowrap',
    },
  }), []);

  const extensions = useMemo(() => {
    const exts: Extension[] = [
      mentionExt,
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightActiveLine(),
      drawSelection(),
      bracketMatching(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      openmateHighlight,
      markdownLang,
      theme,
      history(),
      EditorView.lineWrapping,
      // Enter/Shift-Enter must NOT be in keymap — domEventHandlers handles send logic
      // But CodeMirror's built-in keymap intercepts Enter first.
      // Solution: add Enter handler that calls onKeyDown callback and returns true if handled.
      keymap.of([
        { key: 'Enter', run: (view) => {
          // Build a synthetic KeyboardEvent for the onKeyDown callback
          const e = new KeyboardEvent('keydown', { key: 'Enter', shiftKey: false, bubbles: true });
          if (onKeyDownRef.current && onKeyDownRef.current(e, view)) return true;
          // If not handled (e.g. ctrl-enter mode), insert newline
          view.dispatch(view.state.replaceSelection('\n'));
          return true;
        }},
        { key: 'Shift-Enter', run: (view) => {
          const e = new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true });
          if (onKeyDownRef.current && onKeyDownRef.current(e, view)) return true;
          view.dispatch(view.state.replaceSelection('\n'));
          return true;
        }},
        { key: 'Mod-Enter', run: (view) => {
          const e = new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true });
          if (onKeyDownRef.current && onKeyDownRef.current(e, view)) return true;
          view.dispatch(view.state.replaceSelection('\n'));
          return true;
        }},
        ...historyKeymap,
        ...defaultKeymap.filter(k => k.key !== 'Enter'),
        { key: 'Mod-z', run: undo, preventDefault: true },
        { key: 'Mod-Shift-z', run: redo, preventDefault: true },
        { key: 'Mod-y', run: redo, preventDefault: true },
        { key: 'Tab', run: (view) => {
          view.dispatch(view.state.replaceSelection('  '));
          return true;
        }},
      ]),
    ];
    if (readOnly) {
      exts.push(EditorState.readOnly.of(true));
    }
    return exts;
  }, [placeholder, readOnly, theme]);

  const handleKeyDown = useCallback((e: KeyboardEvent, view: EditorView) => {
    if (onKeyDownRef.current) {
      return onKeyDownRef.current(e, view);
    }
    return false;
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const state = EditorState.create({
      doc: value,
      extensions: [
        ...extensions,
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            userEditingRef.current = true;
            onChangeRef.current(update.state.doc.toString());
            // Reset flag after React has time to process the state update
            requestAnimationFrame(() => { userEditingRef.current = false; });
          }
        }),
        EditorView.domEventHandlers({ keydown: handleKeyDown }),
      ],
    });

    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [extensions, handleKeyDown]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    // Skip if the change came from user typing (avoids cursor jump race condition)
    if (userEditingRef.current) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({
        changes: { from: 0, to: current.length, insert: value },
      });
    }
  }, [value]);

  const focus = useCallback(() => {
    viewRef.current?.focus();
  }, []);

  return (
    <div
      style={{ position: 'relative', width: '100%' }}
      onClick={focus}
    >
      <div
        ref={containerRef}
        className={`codemirror-editor ${className}`}
      />
      {value.length === 0 && placeholder && (
        <div style={{
          position: 'absolute',
          top: '4px',
          left: '24px',
          color: 'hsl(var(--muted-foreground) / 0.4)',
          fontStyle: 'italic',
          pointerEvents: 'none',
          fontSize: '14px',
          lineHeight: '1.6',
          whiteSpace: 'pre-wrap',
        }}>
          {placeholder}
        </div>
      )}
    </div>
  );
}
