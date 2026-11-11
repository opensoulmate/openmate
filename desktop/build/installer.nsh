; OpenMate 安装器自定义段
; 修的是上游隐藏门：createDesktopShortcut:"always" 的建图分支被 ${ifNot} ${isUpdated}
; 挡住——electron-updater 的更新安装（quitAndInstall/autoInstallOnAppQuit）永远走不进
; 建图逻辑，桌面图标静默丢失。本宏在任何安装（含自动更新）收尾处强制重建桌面图标。
!macro customInstall
  DetailPrint "openmate-icon-fix:customInstall"
  CreateShortCut "$DESKTOP\${SHORTCUT_NAME}.lnk" "$appExe" "" "$appExe" 0 "" "" "${APP_DESCRIPTION}"
  ClearErrors
  WinShell::SetLnkAUMI "$DESKTOP\${SHORTCUT_NAME}.lnk" "${APP_ID}"
  System::Call 'Shell32::SHChangeNotify(i 0x8000000, i 0, i 0, i 0)'
!macroend
