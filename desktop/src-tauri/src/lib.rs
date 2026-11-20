// lib.rs: הצד של Tauri (Rust). שלושה תפקידים:
//   1. להפעיל את server.exe (ה-sidecar של Python) כשהאפליקציה נפתחת
//   2. לסגור אותו כשהאפליקציה נסגרת, כדי שלא יישאר תהליך "יתום" ברקע
//   3. פקודה אחת ש-React יכול לקרוא לה: שמירת קובץ טקסט (בשביל תקציר השיחה)
//
// את כל השאר (האייג'נט, הגרף, ה-AI) עושה שרת ה-Python. Rust רק "מארח".

use std::sync::Mutex;
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

// כאן נשמר "הידית" לתהליך של השרת, כדי שנוכל לסגור אותו ביציאה
struct Sidecar(Mutex<Option<CommandChild>>);

/// נקרא מ-React: invoke("save_text_file", { path, contents })
#[tauri::command]
fn save_text_file(path: String, contents: String) -> Result<(), String> {
    std::fs::write(&path, contents).map_err(|e| format!("השמירה נכשלה: {e}"))
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Sidecar(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![save_text_file])
        .setup(|app| {
            // "server" = השם שהוגדר ב-externalBin ב-tauri.conf.json.
            // Tauri מוצא לבד את הקובץ server-<target>.exe שארזנו עם האפליקציה
            let (_events, child) = app
                .shell()
                .sidecar("server")
                .expect("server sidecar is missing from the bundle")
                // השרת "שומר" על ה-PID שלנו, ונסגר לבד כשהאפליקציה נסגרת (או קורסת)
                .args(["--parent-pid", &std::process::id().to_string()])
                .spawn()
                .expect("failed to start the LogSentinel server");
            *app.state::<Sidecar>().0.lock().unwrap() = Some(child);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        // ביציאה מהאפליקציה: סוגרים את השרת
        if let RunEvent::Exit = event {
            if let Some(child) = handle.state::<Sidecar>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
