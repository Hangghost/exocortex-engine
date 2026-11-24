// SessionStart hook: warn when the persona plugin a workspace declares is not usable here.
// Contract: docs/exo-persona.md. Node built-ins only; no bash, no symlinks, no python.
//
// Opt-in: the workspace declares `[persona] plugin = "<name>@<marketplace>"` in
// .exocortex/instance.toml. Without that section this prints nothing and exits 0.
// States: present (silent) | absent | disabled | unknown. Anything but present speaks,
// and `unknown` speaks too: a broken detector must not look like "all fine".
// Always exits 0 (a SessionStart hook must never block the session).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const MSG = {
  absent: (c) =>
    `[exo] persona plugin 未安裝（absent）：這個工作區期望 ${c.id}，但「這個設定目錄」沒有安裝它。\n` +
    `設定目錄：${c.configDir}\n` +
    `請在使用這個設定目錄的視窗執行：claude plugin install ${c.id}\n` +
    `（不想用 persona 時，把 .exocortex/instance.toml 的 [persona] 段註解掉即可。）`,
  disabled: (c) =>
    `[exo] persona plugin 已停用（disabled）：${c.id} 已安裝，但在 ${c.where} 被設為停用。\n` +
    `設定目錄：${c.configDir}\n` +
    `請執行：claude plugin enable ${c.id}`,
  unknown: (c) =>
    `[exo] 無法判斷 persona plugin 是否在場（unknown）：${c.reason}\n` +
    `這個工作區期望 persona plugin${c.id ? ` ${c.id}` : ""}；偵測器讀不到足夠資訊，所以不保證它在場。\n` +
    `細節見 docs/exo-persona.md。`,
};

function readText(file) {
  return fs.readFileSync(file, "utf8").replace(/^﻿/, "");
}

function readJson(file) {
  return JSON.parse(readText(file));
}

function readStdin() {
  if (process.stdin.isTTY) return {};
  try {
    const raw = fs.readFileSync(0, "utf8").trim();
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

const same = (a, b) => {
  const [x, y] = [path.resolve(a), path.resolve(b)];
  return process.platform === "win32" || process.platform === "darwin"
    ? x.toLowerCase() === y.toLowerCase()
    : x === y;
};

// <config dir>/plugins/cache/... -> <config dir>; null when the plugin root is not under one.
function configDirFromPluginRoot(root) {
  const parts = path.resolve(root).split(path.sep);
  for (let i = parts.length - 2; i >= 1; i--) {
    if (parts[i] === "plugins" && parts[i + 1] === "cache") {
      return parts.slice(0, i).join(path.sep) || path.sep;
    }
  }
  return null;
}

// Returns { dir } or { unknown: reason }.
function resolveConfigDir(env) {
  const fromEnv = env.CLAUDE_CONFIG_DIR || null;
  const root = env.CLAUDE_PLUGIN_ROOT || null;
  const fromRoot = root ? configDirFromPluginRoot(root) : null;
  if (fromEnv && fromRoot) {
    return same(fromEnv, fromRoot)
      ? { dir: path.resolve(fromEnv) }
      : { unknown: `CLAUDE_CONFIG_DIR（${fromEnv}）與 CLAUDE_PLUGIN_ROOT 推得的設定目錄（${fromRoot}）不一致` };
  }
  if (fromEnv) return { dir: path.resolve(fromEnv) };
  if (fromRoot) return { dir: fromRoot };
  if (root) return { unknown: `CLAUDE_PLUGIN_ROOT（${root}）不在 <設定目錄>/plugins/cache 之下，且沒有 CLAUDE_CONFIG_DIR` };
  return { dir: path.join(os.homedir(), ".claude") };
}

function findInstanceToml(startDir) {
  let dir = path.resolve(startDir);
  for (;;) {
    const file = path.join(dir, ".exocortex", "instance.toml");
    if (fs.existsSync(file)) return { file, workspace: dir };
    const up = path.dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

// Line-wise read of the optional [persona] table. Returns
//   { declared: false } | { declared: true, id } | { declared: true, unknown: reason }
function parsePersona(text) {
  let inPersona = false;
  let seen = false;
  let id = null;
  let broken = null;
  for (const raw of text.replace(/^﻿/, "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    let m;
    if ((m = line.match(/^\[\[\s*persona\s*\]\]/)) || line.match(/^\[\s*persona\s*\./)) {
      return { declared: true, unknown: "instance.toml 的 [persona] 寫成不支援的形式（只認 [persona] 段下的 plugin = \"…\"）" };
    }
    if (line.startsWith("[")) {
      inPersona = /^\[\s*persona\s*\]\s*(#.*)?$/.test(line);
      if (inPersona) seen = true;
      continue;
    }
    if (!inPersona) {
      if (/^persona\s*[.=]/.test(line)) {
        return { declared: true, unknown: "instance.toml 的 persona 寫成不支援的形式（只認 [persona] 段下的 plugin = \"…\"）" };
      }
      continue;
    }
    m = line.match(/^plugin\s*=\s*(?:"([^"]*)"|'([^']*)')\s*(#.*)?$/);
    if (m) {
      id = (m[1] ?? m[2]).trim();
    } else if (/^plugin\s*=/.test(line)) {
      broken = "instance.toml 的 [persona] 段 plugin 不是單行字串";
    }
  }
  if (!seen) return { declared: false };
  if (broken) return { declared: true, unknown: broken };
  if (!id) return { declared: true, unknown: "instance.toml 有 [persona] 段，但取不出 plugin = \"<name>@<marketplace>\"" };
  if (!/^[^@\s]+@[^@\s]+$/.test(id)) {
    return { declared: true, id, unknown: `[persona] plugin 的值 ${JSON.stringify(id)} 不是 <name>@<marketplace>` };
  }
  return { declared: true, id };
}

// -> { state: "present" | "absent" | "disabled" | "unknown", ... }
function classify({ id, configDir, workspace }) {
  const listFile = path.join(configDir, "plugins", "installed_plugins.json");
  let list;
  try {
    list = readJson(listFile);
  } catch (e) {
    const why = e.code === "ENOENT" ? "不存在" : e instanceof SyntaxError ? "不是合法 JSON" : `讀取失敗（${e.code || e.message}）`;
    return { state: "unknown", reason: `${listFile} ${why}` };
  }
  if (!list || list.version !== 2 || typeof list.plugins !== "object" || list.plugins === null || Array.isArray(list.plugins)) {
    return { state: "unknown", reason: `${listFile} 的格式不是已知的 version 2（Claude Code 可能改了格式）` };
  }
  const entry = list.plugins[id];
  if (!Array.isArray(entry) || entry.length === 0) return { state: "absent" };

  const layers = [
    ["user 設定", path.join(configDir, "settings.json")],
    ["專案設定（.claude/settings.json）", path.join(workspace, ".claude", "settings.json")],
    ["本機設定（.claude/settings.local.json）", path.join(workspace, ".claude", "settings.local.json")],
  ];
  for (const [label, file] of layers) {
    let settings;
    try {
      settings = readJson(file);
    } catch (e) {
      if (e.code === "ENOENT") continue;
      return { state: "unknown", reason: `${file} ${e instanceof SyntaxError ? "不是合法 JSON" : `讀取失敗（${e.code || e.message}）`}` };
    }
    if (settings && typeof settings.enabledPlugins === "object" && settings.enabledPlugins && settings.enabledPlugins[id] === false) {
      return { state: "disabled", where: label };
    }
  }
  return { state: "present" };
}

function emit(message) {
  const output = {
    systemMessage: message,
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext:
        "請在你對使用者的第一個回覆開頭，先原文轉述下列警告（一次即可），再處理使用者的需求：\n" + message,
    },
  };
  process.stdout.write(JSON.stringify(output) + "\n");
}

function main() {
  const input = readStdin();
  const found = findInstanceToml(process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd());
  if (!found) return;
  let declared;
  try {
    declared = parsePersona(readText(found.file));
  } catch (e) {
    emit(MSG.unknown({ reason: `${found.file} 讀取失敗（${e.code || e.message}）` }));
    return;
  }
  if (!declared.declared) return;
  if (declared.unknown) {
    emit(MSG.unknown({ id: declared.id, reason: declared.unknown }));
    return;
  }
  const cfg = resolveConfigDir(process.env);
  if (cfg.unknown) {
    emit(MSG.unknown({ id: declared.id, reason: cfg.unknown }));
    return;
  }
  const result = classify({ id: declared.id, configDir: cfg.dir, workspace: found.workspace });
  if (result.state === "present") return;
  emit(MSG[result.state]({ id: declared.id, configDir: cfg.dir, where: result.where, reason: result.reason }));
}

try {
  main();
} catch (e) {
  emit(MSG.unknown({ reason: `偵測器內部錯誤：${e && e.message ? e.message : e}` }));
}
