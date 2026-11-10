use serde::Serialize;
use std::process::Command;
use tauri::Emitter;
use tauri::Manager;

#[derive(Serialize)]
struct SystemInfo {
    os: String,
    arch: String,
    home_dir: String,
}

#[tauri::command]
fn get_system_info() -> SystemInfo {
    SystemInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        home_dir: dirs::home_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default(),
    }
}

#[tauri::command]
fn greet(name: String) -> String {
    format!("Hello, {}! Welcome to OpenMate.", name)
}

#[tauri::command]
fn execute_command(cmd: String) -> Result<String, String> {
    let output = if cfg!(target_os = "windows") {
        Command::new("cmd")
            .args(["/C", &cmd])
            .output()
    } else {
        Command::new("sh")
            .args(["-c", &cmd])
            .output()
    };

    match output {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout).to_string();
            let stderr = String::from_utf8_lossy(&out.stderr).to_string();
            if out.status.success() {
                Ok(stdout)
            } else {
                Err(format!("{}\n{}", stdout, stderr).trim().to_string())
            }
        }
        Err(e) => Err(format!("Failed to execute command: {}", e)),
    }
}

#[tauri::command]
fn read_local_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read file '{}': {}", path, e))
}

#[tauri::command]
fn write_local_file(path: String, content: String) -> Result<(), String> {
    if let Some(parent) = std::path::Path::new(&path).parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create directory: {}", e))?;
    }
    std::fs::write(&path, &content)
        .map_err(|e| format!("Failed to write file '{}': {}", path, e))
}

#[tauri::command]
fn list_directory(path: String) -> Result<Vec<String>, String> {
    let entries = std::fs::read_dir(&path)
        .map_err(|e| format!("Failed to read directory '{}': {}", path, e))?;

    let mut result = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|e| format!("Failed to read entry: {}", e))?;
        let file_name = entry.file_name().to_string_lossy().to_string();
        let file_type = entry.file_type()
            .map_err(|e| format!("Failed to read file type: {}", e))?;
        if file_type.is_dir() {
            result.push(format!("{}/", file_name));
        } else {
            result.push(file_name);
        }
    }
    result.sort();
    Ok(result)
}

#[tauri::command]
fn set_page_title(window: tauri::Window, title: String) {
    let _ = window.set_title(&title);
}

#[tauri::command]
fn minimize_window(window: tauri::Window) {
    #[cfg(desktop)]
    let _ = window.minimize();
    #[cfg(not(desktop))]
    let _ = window;
}

#[tauri::command]
fn toggle_maximize_window(window: tauri::Window) {
    #[cfg(desktop)]
    {
        if window.is_maximized().unwrap_or(false) {
            let _ = window.unmaximize();
        } else {
            let _ = window.maximize();
        }
    }
    #[cfg(not(desktop))]
    let _ = window;
}

struct SplashState {
    done: std::sync::atomic::AtomicBool,
    start: std::time::Instant,
}

#[cfg(desktop)]
fn handoff_splash(app: &tauri::AppHandle) {
    use std::sync::atomic::Ordering;
    let st = app.state::<SplashState>();
    if st.done.swap(true, Ordering::SeqCst) {
        return;
    }
    let wait_min = std::time::Duration::from_millis(1400).saturating_sub(st.start.elapsed());
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(360) + wait_min);
        if let Some(main) = app.get_webview_window("main") {
            let _ = main.show();
            let _ = main.set_focus();
        }
        if let Some(sw) = app.get_webview_window("splash") {
            let _ = sw.close();
        }
    });
}

/// 前端 React 挂载完成后报到（shell-title-bar 挂载即调）：
/// 72%(RENDERING)→100%(READY)→交接主窗口。tauri 2.11 的 on_page_load 只能
/// 构造期挂（Builder 链式吃 self），已建的 conf 主窗挂不上——前端报到语义更准。
#[tauri::command]
fn splash_ready(app: tauri::AppHandle) {
    #[cfg(desktop)]
    {
        if let Some(sw) = app.get_webview_window("splash") {
            let _ = sw.eval("window.setSplash && window.setSplash(72,2)");
        }
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(400));
            if let Some(sw) = app.get_webview_window("splash") {
                let _ = sw.eval("window.setSplash && window.setSplash(100,3)");
            }
            handoff_splash(&app);
        });
    }
    #[cfg(not(desktop))]
    let _ = app;
}

#[tauri::command]
fn is_maximized_window(window: tauri::Window) -> bool {
    #[cfg(desktop)]
    let v = window.is_maximized().unwrap_or(false);
    #[cfg(not(desktop))]
    let v = {
        let _ = &window;
        false
    };
    v
}

#[tauri::command]
fn close_window(window: tauri::Window) {
    // 关闭=收进托盘常驻（托盘菜单"退出"才真退出）
    let _ = window.hide();
}

#[cfg(desktop)]
fn show_main(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .setup(|app| {
            // ── 主窗口改 Rust 构造（conf 不再定义窗口）──
            // tauri 2.11 的 on_new_window 是构造期链式方法（mut self→Self），conf 建的窗
            // 挂不上：workspace 浏览器里 target=_blank / window.open 被静默吞（用户报障
            // "点百度链接没响应"）。此处拦截新窗请求 → 转发前端开 workspace 新 web-browser
            // 标签（Electron 时代 main.js 拦 new-window 的同款行为）。
            let handle_new_win = app.handle().clone();
            tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::App("".into()))
                .title("OpenMate｜开放协同的AI智能伙伴")
                .decorations(false)
                .transparent(true)
                .visible(false)
                .inner_size(1440.0, 900.0)
                .min_inner_size(375.0, 600.0)
                .resizable(true)
                .center()
                .on_new_window(move |url, _features| {
                    eprintln!("[newwin] 拦截到新窗请求: {}", url);
                    let _ = handle_new_win.emit("open-web-tab", url.to_string());
                    tauri::webview::NewWindowResponse::Deny
                })
                .build()?;
            #[cfg(debug_assertions)]
            {
                let window = app.get_webview_window("main").unwrap();
                window.open_devtools();
            }
            #[cfg(desktop)]
            {
                // ── 启动 splash：Electron 版同款打字机加载动画（splash.html 像素校准稿原样复用）──
                use std::sync::atomic::AtomicBool;
                use std::time::{Duration, Instant};
                fn splash_prog(w: &tauri::WebviewWindow, pct: u32, stage: u32) {
                    let _ = w.eval(&format!("window.setSplash && window.setSplash({pct},{stage})"));
                }
                let splash_start = Instant::now();
                let splash_win = tauri::WebviewWindowBuilder::new(
                    app,
                    "splash",
                    tauri::WebviewUrl::App("splash.html".into()),
                )
                .title("OpenMate")
                .inner_size(560.0, 360.0)
                .resizable(false)
                .decorations(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .center()
                .build()?;
                splash_prog(&splash_win, 8, 0);
                app.manage(SplashState {
                    done: AtomicBool::new(false),
                    start: splash_start,
                });
                std::thread::sleep(Duration::from_millis(150));
                splash_prog(&splash_win, 38, 1);
                {
                    // 页面 12s 未就绪也强制交接（splash 不能永久遮屏）
                    let app = app.handle().clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(Duration::from_secs(12));
                        if let Some(sw) = app.get_webview_window("splash") {
                            splash_prog(&sw, 100, 3);
                        }
                        handoff_splash(&app);
                    });
                }
                use tauri::menu::{Menu, MenuItem};
                use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
                let show = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
                let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
                let menu = Menu::with_items(app, &[&show, &quit])?;
                if let Some(icon) = app.default_window_icon().cloned() {
                    let tray = TrayIconBuilder::with_id("main-tray")
                        .icon(icon)
                        .tooltip("OpenMate")
                        .menu(&menu)
                        .show_menu_on_left_click(false)
                        .on_menu_event(|app, event| match event.id.as_ref() {
                            "show" => show_main(app),
                            "quit" => app.exit(0),
                            _ => {}
                        })
                        .on_tray_icon_event(|tray, event| {
                            if let TrayIconEvent::Click {
                                button: MouseButton::Left,
                                button_state: MouseButtonState::Up,
                                ..
                            } = event
                            {
                                show_main(tray.app_handle());
                            }
                        })
                        .build(app)?;
                    // 根修：TrayIcon 是 RAII（Drop=托盘设 Passive+删图标文件），必须保活到应用生命周期结束
                    app.manage(tray);
                }
                if let Some(win) = app.get_webview_window("main") {
                    let w = win.clone();
                    win.on_window_event(move |event| {
                        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                            api.prevent_close();
                            let _ = w.hide();
                        }
                    });
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_system_info,
            greet,
            execute_command,
            read_local_file,
            write_local_file,
            list_directory,
            set_page_title,
            minimize_window,
            toggle_maximize_window,
            is_maximized_window,
            splash_ready,
            close_window
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
