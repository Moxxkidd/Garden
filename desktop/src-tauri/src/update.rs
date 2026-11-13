use crate::{control, engine, runtime, Shell};
use serde_json::{json, Value};
use std::{
    fs,
    process::{Command, Stdio},
    sync::atomic::Ordering,
    time::Duration,
};
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

pub async fn check(app: &AppHandle) -> Result<Value, String> {
    let config = app.config();
    let channel = config.plugins.0.get("updater");
    if channel
        .and_then(|c| c.get("pubkey"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .is_empty()
    {
        return Err(
            "此本地 Beta 未配置正式更新渠道与公钥；未发起外部检查。正式发布签名/公证仍待单独授权。"
                .into(),
        );
    }
    let update = app
        .updater_builder()
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?;
    let result = match &update {
        Some(u) => json!({"available":true,"version":u.version,"body":u.body}),
        None => json!({"available":false}),
    };
    *app.state::<Shell>().update.lock().unwrap() = update;
    Ok(result)
}
pub async fn install(app: &AppHandle) -> Result<Value, String> {
    let active = runtime(app)?;
    if !active.owned {
        return Err(
            "当前引擎由 CLI/外部服务持有；请先用原入口停止，再由 App 启动后更新。未终止外部实例。"
                .into(),
        );
    }
    let update = app
        .state::<Shell>()
        .update
        .lock()
        .unwrap()
        .take()
        .ok_or("请先检查并确认一个可用更新")?;
    if control(app, &active, "activity", None)?["idle"] != true {
        return Err(
            "请等待活动采集、人工登录和导出结束，或先明确停止任务，再重新检查更新。".into(),
        );
    }
    // The official plugin validates the signature during download, before any data or program replacement.
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let bundle = executable
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .parent()
        .unwrap();
    let configuration = app.state::<Shell>().config.clone();
    let value = engine(
        app,
        &[
            "update-prepare",
            "--home",
            &active.home,
            "--app",
            bundle.to_str().unwrap(),
            "--expected-version",
            &update.version,
            "--instance",
            &active.instance_id,
            "--config",
            configuration.to_str().unwrap(),
        ],
        None,
    )?;
    let plan = value["plan"]
        .as_str()
        .ok_or("更新备份未返回计划")?
        .to_string();
    let guardian = value["guardian"].as_str().ok_or("更新备份未返回恢复入口")?;
    Command::new(guardian)
        .args(["recover", "--plan", &plan])
        .env("PYINSTALLER_RESET_ENVIRONMENT", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    control(app, &active, "stop", None)?;
    if let Err(error) = update.install(bytes) {
        fs::write(format!("{plan}.cancelled"), error.to_string()).map_err(|e| e.to_string())?;
        return Err(format!(
            "更新未安装：{error}。本地引擎已暂停，请退出后重新打开 App。备份保留在 {plan}"
        ));
    }
    fs::write(format!("{plan}.installed"), b"installed").map_err(|e| e.to_string())?;
    app.state::<Shell>().exiting.store(true, Ordering::SeqCst);
    app.exit(0);
    Ok(json!({"installed":true,"restart":"guarded","backup_plan":plan}))
}
pub fn record_startup_instance(active: &crate::Runtime) -> Result<(), String> {
    let args: Vec<_> = std::env::args().collect();
    if let Some(pair) = args.windows(2).find(|w| w[0] == "--update-plan") {
        fs::write(
            format!("{}.engine", pair[1]),
            json!({"instance_id":active.instance_id,"owned":active.owned}).to_string(),
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
pub fn acknowledge_startup(app: &AppHandle) -> Result<(), String> {
    let args: Vec<_> = std::env::args().collect();
    if let Some(pair) = args.windows(2).find(|w| w[0] == "--update-plan") {
        let path = &pair[1];
        let plan: Value = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
        if plan["expected_version"] != app.package_info().version.to_string() {
            return Err("更新启动版本与备份计划不一致".into());
        }
        fs::write(
            format!("{path}.ready"),
            app.package_info().version.to_string(),
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
