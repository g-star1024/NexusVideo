/**
 * install.ts — 一键拉取状态机（Pinia Store）
 * ============================================================
 * 管理设置中心「一键拉取」的完整生命周期：
 *   idle → pulling → done | failed | warning
 *
 * 设计要点（小白优先）：
 *   - target_dir：默认 D:/nexusvideo_staging，持久化到 localStorage，刷新不丢
 *   - message：原样展示后端下发的「人话」文案，绝不在 UI 暴露 current/total 数字
 *   - 失败/警告后可「重试」：再次 POST /install/pull（同一 target_dir，后端断点续传）
 *   - 完成 → 由组件按钮跳转生成页（store 不耦合 router）
 *
 * SSE：EventSource 实例保存在闭包变量（非响应式），终态后主动 close。
 * 导航离开再回来时（组件 onMounted）调用 ensureSubscription 自愈重连——
 * 后端进度总线会回放最近一条事件，避免 UI 卡在旧状态。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import {
  pullInstall,
  subscribeInstallProgress,
  type InstallProgressEvent,
  type InstallStage,
} from '../api/install';

export type InstallStatus = 'idle' | 'pulling' | 'done' | 'failed' | 'warning';

const STORAGE_KEY = 'nexus_install_dir';
const DEFAULT_DIR = 'D:/nexusvideo_staging';

export const useInstallStore = defineStore('install', () => {
  // ---------- 响应式状态 ----------
  const targetDir = ref<string>(
    localStorage.getItem(STORAGE_KEY) || DEFAULT_DIR,
  );
  const taskId = ref<string | null>(null);
  const status = ref<InstallStatus>('idle');
  const stage = ref<InstallStage | null>(null);
  /** 后端下发的「人话」文案，直接展示给用户 */
  const message = ref<string>('');
  /** 自检警告时缺失的模型相对路径清单（仅 warning 态非空） */
  const missing = ref<string[]>([]);
  /**
   * 技术细节（原始 error），仅用于 console / 日志，绝不直接展示给用户，
   * 满足设计系统「错误信息吓人」风险点的硬性要求。
   */
  const errorDetail = ref<string | null>(null);

  // ---------- 非响应式（SSE 实例） ----------
  let es: EventSource | null = null;
  let mockTimer: ReturnType<typeof setTimeout> | null = null;

  // ---------- 计算属性 ----------
  const isPulling = computed(() => status.value === 'pulling');
  const isTerminal = computed(
    () => status.value === 'done' || status.value === 'failed' || status.value === 'warning',
  );
  const canStart = computed(() => status.value === 'idle' || status.value === 'failed' || status.value === 'warning');

  // ---------- 内部：关闭 SSE ----------
  function closeEs() {
    if (es) {
      es.onmessage = null;
      es.onerror = null;
      es.close();
      es = null;
    }
  }

  // ---------- 内部：事件归约（状态机核心，纯函数风格） ----------
  function applyEvent(ev: InstallProgressEvent) {
    // 终态保护：已经进入终态后忽略后续事件（EventSource 可能重复推送）
    if (isTerminal.value) return;

    // 1) 失败
    if (ev.failed) {
      status.value = 'failed';
      message.value =
        ev.message || '网络不太顺，已停在这一步，点重试继续（不会重新下载已完成部分）';
      errorDetail.value = ev.error ?? null;
      if (ev.task_id) taskId.value = ev.task_id;
      closeEs();
      return;
    }

    // 2) 自检警告（拉完但模型缺失）—— 视为终态 UX，关闭 SSE
    if (ev.warning) {
      status.value = 'warning';
      message.value = ev.message || '组件已就位，但部分模型自检未通过';
      missing.value = ev.missing ?? [];
      if (ev.task_id) taskId.value = ev.task_id;
      closeEs();
      return;
    }

    // 3) 完成：UI 统一展示「准备就绪」（与流式中"即将完成，马上可以出片"区分）
    if (ev.ok && ev.done) {
      status.value = 'done';
      message.value = '准备就绪';
      if (ev.task_id) taskId.value = ev.task_id;
      closeEs();
      return;
    }

    // 4) 进度中：保持 pulling，仅更新文案/阶段（不暴露数字）
    status.value = 'pulling';
    if (ev.stage) stage.value = ev.stage;
    if (ev.message) message.value = ev.message;
    if (ev.task_id) taskId.value = ev.task_id;
  }

  // ---------- Actions ----------

  /** 设置目标目录（带持久化） */
  function setTargetDir(dir: string) {
    const d = dir.trim();
    if (!d) return;
    targetDir.value = d;
    localStorage.setItem(STORAGE_KEY, d);
  }

  /** 触发 / 重试拉取（后端同一 target_dir 断点续传） */
  async function startPull() {
    if (status.value === 'pulling') return;
    errorDetail.value = null;
    missing.value = [];
    status.value = 'pulling';
    // 乐观初始文案（与后端 prepare 阶段一致），避免 SSE 连接前的空窗
    stage.value = 'prepare';
    message.value = '正在准备运行环境…';

    try {
      const res = await pullInstall(targetDir.value || undefined);
      taskId.value = res.task_id;
      // 打开 SSE 订阅进度
      closeEs();
      es = subscribeInstallProgress(
        applyEvent,
        (err) => console.warn('[install] SSE 连接异常', err),
      );
    } catch (e) {
      // POST 失败（后端未起 / 端口不通）：给小白友好提示，仍可重试
      status.value = 'failed';
      message.value = '没能开始拉取，请确认程序已正常启动，然后点重试继续';
      errorDetail.value = e instanceof Error ? e.message : String(e);
      closeEs();
    }
  }

  /** 重试 = 重新触发拉取（同一 target_dir） */
  function retry() {
    return startPull();
  }

  /** 组件卸载时释放 SSE */
  function closeProgress() {
    closeEs();
    if (mockTimer) {
      clearTimeout(mockTimer);
      mockTimer = null;
    }
  }

  /**
   * 导航离开再回来时自愈：若仍在 pulling 但连接已断，重新订阅。
   * 后端进度总线会回放最近一条事件，UI 立即回到正确阶段。
   */
  function ensureSubscription() {
    if (status.value === 'pulling' && es === null) {
      es = subscribeInstallProgress(
        applyEvent,
        (err) => console.warn('[install] SSE 连接异常（重连）', err),
      );
    }
  }

  /** 重置回 idle（不清除 targetDir） */
  function reset() {
    closeProgress();
    status.value = 'idle';
    stage.value = null;
    message.value = '';
    missing.value = [];
    errorDetail.value = null;
    taskId.value = null;
  }

  /**
   * DEV 模式状态机自走（mock SSE）：用脚本化事件驱动同一套 applyEvent，
   * 不需要真实 6.7GB 下载即可验证 idle→pulling→done/failed/warning。
   * 仅在 import.meta.env.DEV 下由组件按钮触发。
   */
  function simulate(scenario: 'done' | 'failed' | 'warning') {
    closeProgress();
    status.value = 'pulling';
    stage.value = 'prepare';
    message.value = '正在准备运行环境…';
    const seq: InstallProgressEvent[] = [
      { stage: 'prepare', message: '正在准备运行环境…' },
      { stage: 'core', message: '正在下载核心程序…' },
      { stage: 'model', message: '正在配置模型，请稍候…' },
      { stage: 'verify', message: '即将完成，马上可以出片' },
    ];
    let i = 0;
    const tick = () => {
      if (i < seq.length) {
        applyEvent(seq[i]);
        i += 1;
        mockTimer = setTimeout(tick, 700);
      } else if (scenario === 'done') {
        applyEvent({ stage: 'verify', message: '即将完成，马上可以出片', ok: true, done: true });
      } else if (scenario === 'failed') {
        applyEvent({
          stage: 'failed',
          message: '网络不太顺，已停在这一步，点重试继续（不会重新下载已完成部分）',
          failed: true,
          error: 'mock-network-error',
        });
      } else {
        applyEvent({
          stage: 'verify',
          message: '组件已就位，但部分模型自检未通过：text_encoders/umt5_xxl_fp8_e4m3fn.safetensors',
          warning: true,
          missing: ['text_encoders/umt5_xxl_fp8_e4m3fn.safetensors'],
        });
      }
    };
    tick();
  }

  return {
    // state
    targetDir,
    taskId,
    status,
    stage,
    message,
    missing,
    errorDetail,
    // getters
    isPulling,
    isTerminal,
    canStart,
    // actions
    setTargetDir,
    startPull,
    retry,
    closeProgress,
    ensureSubscription,
    reset,
    simulate,
  };
});
