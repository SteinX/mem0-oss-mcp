import {createHash} from "node:crypto";
import {
  closeSync,
  chmodSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import {join} from "node:path";

export type CaptureMode = "explicit" | "bounded" | "legacy";

export interface CaptureCandidate {
  messageCount: number;
  text: string;
  explicitRemember: boolean;
  now: Date;
}

export interface CaptureDecision {
  capture: boolean;
  reason: string;
  text?: string;
}

interface AppCaptureState {
  date: string;
  dailyCount: number;
  sessions: Record<string, number>;
  recentHashes: string[];
}

interface CaptureState {
  version: 1;
  apps: Record<string, AppCaptureState>;
}

const STATE_FILE = "opencode-capture-state.json";
const LOCK_FILE = "opencode-capture-state.lock";
const MAX_INPUT_CHARS = 2_000;
const SESSION_LIMIT = 3;
const DAILY_LIMIT = 20;
const RECENT_HASH_LIMIT = 100;

function emptyState(): CaptureState {
  return {version: 1, apps: {}};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCaptureState(value: unknown): value is CaptureState {
  if (!isRecord(value) || value.version !== 1 || !isRecord(value.apps)) return false;

  return Object.values(value.apps).every((app) => {
    if (!isRecord(app)) return false;
    if (typeof app.date !== "string" || typeof app.dailyCount !== "number") return false;
    if (!Number.isInteger(app.dailyCount) || app.dailyCount < 0) return false;
    if (!isRecord(app.sessions) || !Array.isArray(app.recentHashes)) return false;
    if (!Object.values(app.sessions).every((count) => Number.isInteger(count) && Number(count) >= 0)) {
      return false;
    }
    return app.recentHashes.every((hash) => typeof hash === "string");
  });
}

function normalizedHash(text: string): string {
  const normalized = text.normalize("NFKC").replace(/\s+/gu, " ").trim();
  return createHash("sha256").update(normalized).digest("hex");
}

function utcDate(now: Date): string {
  return now.toISOString().slice(0, 10);
}

function requestedMode(env: Record<string, string | undefined>): CaptureMode {
  const value = env.MEM0_OSS_AUTO_CAPTURE_MODE?.trim().toLowerCase();
  return value === "bounded" || value === "legacy" || value === "explicit" ? value : "explicit";
}

export class CapturePolicy {
  readonly mode: CaptureMode;

  private constructor(
    mode: CaptureMode,
    private readonly stateDir: string,
    private readonly appId: string,
    private readonly sessionId: string,
    private state: CaptureState,
  ) {
    this.mode = mode;
  }

  static fromEnv(
    env: Record<string, string | undefined>,
    stateDir: string,
    appId: string,
    sessionId: string,
  ): CapturePolicy {
    const mode = requestedMode(env);
    if (mode !== "bounded") {
      return new CapturePolicy(mode, stateDir, appId, sessionId, emptyState());
    }

    const statePath = join(stateDir, STATE_FILE);
    if (!existsSync(statePath)) {
      return new CapturePolicy(mode, stateDir, appId, sessionId, emptyState());
    }

    try {
      const state: unknown = JSON.parse(readFileSync(statePath, "utf8"));
      if (!isCaptureState(state)) throw new Error("invalid capture state schema");
      return new CapturePolicy(mode, stateDir, appId, sessionId, state);
    } catch {
      // Corrupt policy state must never silently re-enable automatic capture.
      return new CapturePolicy("explicit", stateDir, appId, sessionId, emptyState());
    }
  }

  claimCapture(candidate: CaptureCandidate): CaptureDecision {
    const {messageCount, text, explicitRemember, now} = candidate;

    if (explicitRemember) {
      return {capture: false, reason: "explicit_tool_required"};
    }
    if (this.mode === "explicit") {
      return {capture: false, reason: "explicit_mode"};
    }
    if (text.length > MAX_INPUT_CHARS) {
      return {capture: false, reason: "input_too_large"};
    }
    if (!text.trim()) {
      return {capture: false, reason: "empty_input"};
    }

    if (this.mode === "legacy") {
      return messageCount % 3 === 0
        ? {capture: true, reason: "legacy_interval", text}
        : {capture: false, reason: "interval"};
    }

    if (messageCount % 10 !== 0) {
      return {capture: false, reason: "interval"};
    }

    mkdirSync(this.stateDir, {recursive: true, mode: 0o700});
    chmodSync(this.stateDir, 0o700);
    const lockPath = join(this.stateDir, LOCK_FILE);
    let lock: number;
    try {
      lock = openSync(lockPath, "wx", 0o600);
      chmodSync(lockPath, 0o600);
    } catch {
      return {capture: false, reason: "state_lock_unavailable"};
    }

    try {
      if (!this.reloadState()) {
        return {capture: false, reason: "state_invalid"};
      }
      const app = this.appState(now);
      if ((app.sessions[this.sessionId] ?? 0) >= SESSION_LIMIT) {
        return {capture: false, reason: "session_limit"};
      }
      if (app.dailyCount >= DAILY_LIMIT) {
        return {capture: false, reason: "daily_limit"};
      }
      const hash = normalizedHash(text);
      if (app.recentHashes.includes(hash)) {
        return {capture: false, reason: "duplicate"};
      }

      // Claim before the asynchronous MCP write begins. Failed writes may
      // consume quota, which is safer than reopening an automatic-write race.
      app.dailyCount += 1;
      app.sessions[this.sessionId] = (app.sessions[this.sessionId] ?? 0) + 1;
      app.recentHashes.push(hash);
      app.recentHashes = app.recentHashes.slice(-RECENT_HASH_LIMIT);
      this.persist();
      return {capture: true, reason: "eligible", text};
    } finally {
      closeSync(lock);
      unlinkSync(lockPath);
    }
  }

  private reloadState(): boolean {
    const statePath = join(this.stateDir, STATE_FILE);
    if (!existsSync(statePath)) {
      this.state = emptyState();
      return true;
    }
    try {
      const state: unknown = JSON.parse(readFileSync(statePath, "utf8"));
      if (!isCaptureState(state)) return false;
      this.state = state;
      return true;
    } catch {
      return false;
    }
  }

  private appState(now: Date): AppCaptureState {
    const date = utcDate(now);
    let app = this.state.apps[this.appId];
    if (!app) {
      app = {date, dailyCount: 0, sessions: {}, recentHashes: []};
      this.state.apps[this.appId] = app;
    } else if (app.date !== date) {
      app.date = date;
      app.dailyCount = 0;
      app.sessions = {};
    }
    return app;
  }

  private persist(): void {
    mkdirSync(this.stateDir, {recursive: true, mode: 0o700});
    const statePath = join(this.stateDir, STATE_FILE);
    const tempPath = `${statePath}.${process.pid}.${Math.random().toString(16).slice(2)}.tmp`;
    writeFileSync(tempPath, `${JSON.stringify(this.state, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    chmodSync(tempPath, 0o600);
    renameSync(tempPath, statePath);
    chmodSync(statePath, 0o600);
  }
}
