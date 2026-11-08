"use client";

import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import zh from "@/locales/zh.json";
import en from "@/locales/en.json";
import ja from "@/locales/ja.json";

const STORAGE_KEY = "openmate-language";

i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
    ja: { translation: ja },
  },
  lng: "system",
  fallbackLng: "zh",
  supportedLngs: ["zh", "en", "ja", "system"],
  interpolation: {
    escapeValue: false,
  },
});

// 窗口标题跟随语言设置（含 Tauri 桌面壳标题栏同步）
i18n.on("languageChanged", () => {
  if (typeof document !== "undefined") document.title = i18n.t("app.title");
});

export function detectLanguage() {
  if (typeof window === "undefined") return;
  const saved = localStorage.getItem(STORAGE_KEY);
  // "system" 或未设置 → 跟随系统语言
  if (!saved || saved === "system") {
    const langs = navigator.languages || [navigator.language];
    for (const lang of langs) {
      const code = lang.split("-")[0].toLowerCase();
      if (["zh", "en", "ja"].includes(code)) {
        i18n.changeLanguage(code);
        return;
      }
    }
    i18n.changeLanguage("zh");
    return;
  }
  if (["zh", "en", "ja"].includes(saved)) {
    i18n.changeLanguage(saved);
  }
}

export default i18n;
