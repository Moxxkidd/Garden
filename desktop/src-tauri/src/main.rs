use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    fs,
    io::Write,
    path::PathBuf,
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogResult};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;
mod update;

#[derive(Clone, Default, Deserialize, Serialize)]
struct Preferences {
    home: Option<String>,
    notifications: bool,
}
#[derive(Clone, Deserialize)]
struct Runtime {
    home: String,
    browser_url: String,
    port: u16,
    owned: bool,
    instance_id: String,
}
struct Shell {
    preferences: Mutex<Preferences>,
    runtime: Mutex<Option<Runtime>>,
    error: Mutex<String>,
    config: PathBuf,
    exiting: AtomicBool,
    deciding: AtomicBool,
    update: Mutex<Option<tauri_plugin_updater::Update>>,
}
fn shell_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "setup" {
        return Err("原生操作仅允许本地设置窗口".into());
    }
    let url = window.url().map_err(|e| e.to_string())?;
    if !matches!(url.scheme(), "tauri" | "http" | "https")
        || !matches!(url.host_str(), Some("localhost" | "tauri.localhost"))
    {
        return Err("原生操作来源不匹配".into());
    }
    Ok(())
}
fn engine(app: &AppHandle, args: &[&str], payload: Option<Value>) -> Result<Value, String> {
    let executable = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join("engine/garden-engine");
    let mut child = Command::new(executable)
        .args(args)
        .env("PYINSTALLER_RESET_ENVIRONMENT", "1")
        .stdin(if payload.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    if let Some(value) = payload {
        child
            .stdin
            .take()
            .unwrap()
            .write_all(value.to_string().as_bytes())
            .map_err(|e| e.to_string())?;
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).into_owned());
    }
    serde_json::from_slice(&output.stdout).map_err(|e| format!("引擎响应无效：{e}"))
}
fn control(
    app: &AppHandle,
    runtime: &Runtime,
    operation: &str,
    payload: Option<Value>,
) -> Result<Value, String> {
    engine(
        app,
        &[
            "control",
            "--home",
            &runtime.home,
            "--operation",
            operation,
            "--instance",
            &runtime.instance_id,
        ],
        payload,
    )
}
fn runtime(app: &AppHandle) -> Result<Runtime, String> {
    app.state::<Shell>()
        .runtime
        .lock()
        .unwrap()
        .clone()
        .ok_or("请先连接数据目录".into())
}
fn remember(app: &AppHandle, home: String) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    let state = app.state::<Shell>();
    let mut prefs = state.preferences.lock().unwrap();
    prefs.home = Some(home);
    fs::create_dir_all(state.config.parent().unwrap()).map_err(|e| e.to_string())?;
    fs::write(&state.config, serde_json::to_vec(&*prefs).unwrap()).map_err(|e| e.to_string())?;
    fs::set_permissions(&state.config, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())
}
fn show_settings(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("setup") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}
fn show_existing(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    } else {
        show_settings(app);
    }
}
fn notice(app: &AppHandle, text: &str) {
    if app
        .state::<Shell>()
        .preferences
        .lock()
        .unwrap()
        .notifications
    {
        if let Err(error) = app
            .notification()
            .builder()
            .title("Garden")
            .body(text)
            .show()
        {
            eprintln!("通知失败：{error}");
        }
    }
}
fn show_main(app: &AppHandle, active: Runtime) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("main") {
        window.close().map_err(|e| e.to_string())?;
    }
    let origin = format!("http://127.0.0.1:{}", active.port);
    let navigation_origin = origin.clone();
    let opener = app.clone();
    let new_opener = app.clone();
    let window = tauri::WebviewWindowBuilder::new(
        app,
        "main",
        WebviewUrl::External(
            active
                .browser_url
                .parse::<tauri::Url>()
                .map_err(|e| e.to_string())?,
        ),
    )
    .title("Garden · 资产工作台")
    .inner_size(1180., 820.)
    .min_inner_size(640., 520.)
    .on_page_load(|window, payload| {
        if payload.event() == tauri::webview::PageLoadEvent::Finished && payload.url().path() == "/"
        {
            if let Err(error) = update::acknowledge_startup(window.app_handle()) {
                window
                    .app_handle()
                    .dialog()
                    .message(error)
                    .title("更新启动确认失败")
                    .show(|_| {});
            }
        }
    })
    .on_navigation(move |url| {
        let allowed = url.origin().ascii_serialization() == navigation_origin;
        if !allowed && matches!(url.scheme(), "http" | "https") {
            let _ = opener.opener().open_url(url.as_str(), None::<&str>);
        }
        allowed
    })
    .on_new_window(move |url, _| {
        if matches!(url.scheme(), "http" | "https") {
            let _ = new_opener.opener().open_url(url.as_str(), None::<&str>);
        }
        tauri::webview::NewWindowResponse::Deny
    })
    .on_download({
        // WKDownload's completion callback has no path on macOS. Retain the exact
        // private destination chosen at admission, and allow one download per URL.
        let downloads = Mutex::new(HashMap::new());
        move |webview, event| {
            match event {
                tauri::webview::DownloadEvent::Requested {
                    url, destination, ..
                } => {
                    let mut pending = downloads.lock().unwrap();
                    if pending.contains_key(url.as_str()) {
                        return false;
                    }
                    use std::os::unix::fs::PermissionsExt;
                    let active = match runtime(webview.app_handle()) {
                        Ok(active) => active,
                        Err(_) => return false,
                    };
                    let folder = PathBuf::from(active.home).join("runtime/downloads");
                    if fs::create_dir_all(&folder)
                        .and_then(|_| {
                            fs::set_permissions(&folder, fs::Permissions::from_mode(0o700))
                        })
                        .is_err()
                    {
                        return false;
                    }
                    *destination = folder.join(format!(
                        "{}-{}",
                        std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap()
                            .as_nanos(),
                        destination.file_name().unwrap().to_string_lossy()
                    ));
                    pending.insert(url.to_string(), destination.clone());
                }
                tauri::webview::DownloadEvent::Finished { url, success, .. } => {
                    let Some(path) = downloads.lock().unwrap().remove(url.as_str()) else {
                        return false;
                    };
                    let app = webview.app_handle().clone();
                    let parent = webview.window().clone();
                    if !success {
                        if path.exists() {
                            if let Err(error) = fs::remove_file(&path) {
                                eprintln!("失败下载暂存清理失败：{error}");
                            }
                        }
                        app.dialog()
                            .message("下载失败，文件尚未保存。")
                            .parent(&parent)
                            .show(|_| {});
                        return false;
                    }
                    std::thread::spawn(move || {
                        let name = path
                            .file_name()
                            .unwrap()
                            .to_string_lossy()
                            .split_once('-')
                            .unwrap()
                            .1
                            .to_string();
                        let reports = match runtime(&app) {
                            Ok(active) => PathBuf::from(active.home).join("reports"),
                            Err(error) => {
                                app.dialog().message(error).show(|_| {});
                                return;
                            }
                        };
                        let saved = app
                            .dialog()
                            .file()
                            .set_parent(&parent)
                            .set_directory(reports)
                            .set_file_name(name)
                            .blocking_save_file();
                        if let Some(target) = saved {
                            let result =
                                target
                                    .into_path()
                                    .map_err(|e| e.to_string())
                                    .and_then(|target| {
                                        fs::copy(&path, target)
                                            .map(|_| ())
                                            .map_err(|e| e.to_string())
                                    });
                            match result {
                                Ok(()) => {
                                    notice(&app, "导出文件已保存");
                                    app.dialog()
                                        .message("文件已保存到您选择的位置。")
                                        .parent(&parent)
                                        .title("Garden")
                                        .show(|_| {});
                                }
                                Err(e) => {
                                    app.dialog().message(format!("保存失败：{e}")).show(|_| {})
                                }
                            }
                        }
                        if let Err(error) = fs::remove_file(path) {
                            eprintln!("下载暂存清理失败：{error}");
                        }
                    });
                }
                _ => {}
            }
            true
        }
    })
    .build()
    .map_err(|e| e.to_string())?;
    *app.state::<Shell>().runtime.lock().unwrap() = Some(active);
    window.set_focus().map_err(|e| e.to_string())?;
    app.get_webview_window("setup")
        .unwrap()
        .hide()
        .map_err(|e| e.to_string())?;
    Ok(())
}
fn connect_home(app: &AppHandle, home: &str, initialize: bool) -> Result<Value, String> {
    if runtime(app).is_ok() {
        return Err("已有数据目录正在连接；请退出后切换目录，避免并行启动引擎。".into());
    }
    let version = app.package_info().version.to_string();
    let mut args = vec!["connect", "--home", home, "--expected-version", &version];
    if initialize {
        args.push("--initialize");
    }
    let value = engine(app, &args, None)?;
    let active: Runtime = serde_json::from_value(value).map_err(|e| e.to_string())?;
    update::record_startup_instance(&active)?;
    remember(app, active.home.clone())?;
    show_main(app, active)?;
    Ok(json!({"connected":true}))
}
#[tauri::command]
async fn shell_action(
    app: AppHandle,
    window: WebviewWindow,
    action: String,
    payload: Value,
) -> Result<Value, String> {
    shell_window(&window)?;
    if action == "check-update" {
        return update::check(&app).await;
    }
    if action == "install-update" {
        if payload["confirmed"] != true {
            return Err("请明确确认更新与备份".into());
        }
        return update::install(&app).await;
    }
    tauri::async_runtime::spawn_blocking(move || {
        let home = payload.get("home").and_then(Value::as_str).unwrap_or("");
        if matches!(action.as_str(), "inspect" | "connect" | "upgrade" | "restore")
            && home.trim().is_empty()
        {
            return Err("请先选择本地数据目录".into());
        }
        match action.as_str() {
            "state" => {
                let state = app.state::<Shell>();
                Ok(json!({
                    "version": app.package_info().version.to_string(),
                    "preferences": state.preferences.lock().unwrap().clone(),
                    "error": state.error.lock().unwrap().clone(),
                    "connected": state.runtime.lock().unwrap().is_some()
                }))
            }
            "choose-directory" => {
                let path = app.dialog().file().set_parent(&window).blocking_pick_folder()
                    .map(|file| file.into_path().map(|path| path.to_string_lossy().into_owned()))
                    .transpose().map_err(|error| error.to_string())?;
                Ok(json!(path))
            }
            "example" => {
                let active = runtime(&app)?;
                let url = format!("http://127.0.0.1:{}/_garden/example", active.port);
                app.opener().open_url(&url, None::<&str>).map_err(|error| error.to_string())?;
                Ok(json!({"example_url": url, "note": "可在新建扫描中使用此本地合成入口；浏览器采集使用独立 Chromium。"}))
            }
            "inspect" => engine(&app, &["inspect", "--home", home], None),
            "connect" => connect_home(&app, home, payload["initialize"] == true),
            "upgrade" => {
                if payload["confirmed"] != true { return Err("升级需要明确确认".into()); }
                engine(&app, &["upgrade", "--home", home], None)
            }
            "diagnose" => {
                let home = app.state::<Shell>().preferences.lock().unwrap()
                    .home.clone().ok_or("请先选择数据目录")?;
                engine(&app, &["diagnose", "--home", &home], None)
            }
            "notifications" => {
                app.state::<Shell>().preferences.lock().unwrap().notifications = payload["enabled"] == true;
                let home = app.state::<Shell>().preferences.lock().unwrap()
                    .home.clone().ok_or("请先连接数据目录")?;
                remember(&app, home)?;
                Ok(json!({"enabled": payload["enabled"]}))
            }
            "restore" => {
                if payload["confirmed"] != true {
                    return Err("恢复需要确认备份后的修改不会保留；目标必须为空目录".into());
                }
                engine(&app, &["restore-backup", "--home", home], Some(json!({"backup_directory": payload["backup_directory"]})))
            }
            _ => Err("未知原生操作".into())
        }
    })
    .await
    .map_err(|error| error.to_string())?
}
fn close_app(app: &AppHandle) {
    let state = app.state::<Shell>();
    if state.deciding.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let outcome = (|| -> Result<(), String> {
            if let Ok(active) = runtime(&app) {
                if active.owned {
                    let status = match control(&app, &active, "activity", None) {
                        Ok(status) => status,
                        Err(error) => {
                            app.dialog()
                                .message(format!("{error}\n只退出 App，未终止无法验证的引擎。"))
                                .title("本地实例无法验证")
                                .blocking_show();
                            app.state::<Shell>().exiting.store(true, Ordering::SeqCst);
                            app.exit(0);
                            return Ok(());
                        }
                    };
                    let idle = status["idle"] == true;
                    if !idle {
                        let parent = app.get_webview_window("main").ok_or("主窗口尚未就绪")?;
                        parent.show().map_err(|e| e.to_string())?;
                        let result=app.dialog().message("任务仍在运行。继续后台会隐藏窗口；停止并退出会停止新增请求，保留已完成观察并等待任务收尾。").title("Garden · 关闭窗口").parent(&parent)
                            .buttons(MessageDialogButtons::YesNoCancelCustom("继续后台".into(),"停止并退出".into(),"取消".into())).blocking_show_with_result();
                        match result {
                            MessageDialogResult::Custom(label) if label == "继续后台" => {
                                for window in app.webview_windows().values() {
                                    window.hide().map_err(|e| e.to_string())?;
                                }
                                return Ok(());
                            }
                            MessageDialogResult::Custom(label) if label == "停止并退出" => {}
                            _ => return Ok(()),
                        }
                    }
                    control(&app, &active, "stop", None)?;
                }
            }
            app.state::<Shell>().exiting.store(true, Ordering::SeqCst);
            app.exit(0);
            Ok(())
        })();
        if let Err(error) = outcome {
            app.dialog()
                .message(error)
                .title("无法安全退出")
                .show(|_| {});
        }
        app.state::<Shell>().deciding.store(false, Ordering::SeqCst);
    });
}
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            show_existing(app)
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![shell_action])
        .setup(|app| {
            let args: Vec<_> = std::env::args().collect();
            let config = args
                .windows(2)
                .find(|w| w[0] == "--config")
                .map(|w| PathBuf::from(&w[1]))
                .unwrap_or(app.path().app_config_dir()?.join("settings.json"));
            let preferences: Preferences = if config.exists() {
                serde_json::from_slice(&fs::read(&config)?)?
            } else {
                Preferences::default()
            };
            app.manage(Shell {
                preferences: Mutex::new(preferences.clone()),
                runtime: Mutex::new(None),
                error: Mutex::new(String::new()),
                config,
                exiting: AtomicBool::new(false),
                deciding: AtomicBool::new(false),
                update: Mutex::new(None),
            });
            let quit = tauri::menu::MenuItem::with_id(
                app,
                "quit",
                "退出 Garden",
                true,
                Some("CmdOrCtrl+Q"),
            )?;
            let garden_menu = tauri::menu::SubmenuBuilder::new(app, "Garden")
                .text("settings", "App 设置")
                .text("diagnostics", "诊断")
                .text("licenses", "第三方许可证")
                .text("update", "检查更新…")
                .separator()
                .item(&quit)
                .build()?;
            let workbench_menu = tauri::menu::SubmenuBuilder::new(app, "工作台")
                .text("scan", "新建扫描")
                .text("projects", "项目")
                .text("exports", "导出与报告")
                .text("browser", "在浏览器打开")
                .separator()
                .close_window_with_text("关闭窗口")
                .build()?;
            let edit_menu = tauri::menu::SubmenuBuilder::new(app, "编辑")
                .undo()
                .redo()
                .separator()
                .cut()
                .copy()
                .paste()
                .select_all()
                .build()?;
            let menu =
                tauri::menu::Menu::with_items(app, &[&garden_menu, &workbench_menu, &edit_menu])?;
            app.set_menu(menu)?;
            tauri::WebviewWindowBuilder::new(app, "setup", WebviewUrl::App("index.html".into()))
                .title("Garden · 设置与启动")
                .inner_size(800., 780.)
                .build()?;
            if let Some(home) = preferences.home {
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    if let Err(error) = connect_home(&handle, &home, false) {
                        *handle.state::<Shell>().error.lock().unwrap() = error.clone();
                        let _ = handle.emit_to("setup", "startup-error", error);
                        show_settings(&handle);
                    }
                });
            }
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "quit" => close_app(app),
            "settings" | "diagnostics" | "update" => {
                show_settings(app);
                let _ = app.emit_to("setup", "menu-action", event.id().as_ref());
            }
            "licenses" => {
                if let Ok(root) = app.path().resource_dir() {
                    let _ = app.opener().open_path(
                        root.join("notices/README.txt").to_string_lossy(),
                        None::<&str>,
                    );
                }
            }
            "browser" => {
                if let Ok(active) = runtime(app) {
                    let _ = app.opener().open_url(active.browser_url, None::<&str>);
                }
            }
            id @ ("scan" | "projects" | "exports") => {
                if let (Some(w), Ok(active)) = (app.get_webview_window("main"), runtime(app)) {
                    let path = match id {
                        "scan" => "/",
                        "projects" => "/projects",
                        _ => "/reports",
                    };
                    let _ = w.navigate(
                        format!("http://127.0.0.1:{}{path}", active.port)
                            .parse()
                            .unwrap(),
                    );
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            _ => {}
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if !window
                    .app_handle()
                    .state::<Shell>()
                    .exiting
                    .load(Ordering::SeqCst)
                {
                    api.prevent_close();
                    close_app(window.app_handle());
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Garden desktop initialization failed")
        .run(|app, event| {
            if let tauri::RunEvent::Reopen { .. } = event {
                show_existing(app);
            }
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                if !app.state::<Shell>().exiting.load(Ordering::SeqCst) {
                    api.prevent_exit();
                    close_app(app);
                }
            }
        });
}
