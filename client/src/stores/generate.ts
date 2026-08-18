/*
 * NexusVideo 生成状态 Pinia Store
 * ============================================================
 * 负责管理：
 *   - 当前任务状态 (idle / generating / completed / failed)
 *   - 历史作品列表（从 localStorage 持久化）
 *   - 本地/云端模式切换状态
 *   - 后端健康状态
 *
 * 方案 B 架构说明：
 *   - 进度数据由 useProgress 通过 WebSocket 直连获取
 *   - Store 仅管理状态机（idle → generating → completed/failed）
 *   - 生成完成时，通过 buildVideoUrl 将 output_path 转为 HTTP URL
 *   - Tauri IPC 终态事件（task://completed, task://failed）仍作为备用信号
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import {
  generate,
  type GenerateParams,
  type TaskStatus,
  getBackendStatus,
  type BackendStatus,
  buildVideoUrl,
} from '../api/nexus';
import {
  cloudGenerate,
  type CloudGenerateResponse,
  type CloudGenerateController,
} from '../api/cloud';
import { useAuthStore } from './auth';

// ---- 类型定义 ----
export type GenerateMode = 't2v' | 'i2v' | 'v2v';
export type GenerateState = 'idle' | 'generating' | 'completed' | 'failed';
export type CloudMode = 'local' | 'cloud';

export interface HistoryItem {
  id: string;
  mode: GenerateMode;
  prompt: string;
  thumbnailUrl: string;
  videoUrl: string;
  resolution: string;
  durationSec: number;
  renderMode: '本地GPU' | '云端加速';
  createdAt: string; // ISO 时间戳
}

export const useGenerateStore = defineStore('generate', () => {
  // ---- 状态 ----
  const state = ref<GenerateState>('idle');
  const mode = ref<GenerateMode>('t2v');
  const cloudMode = ref<CloudMode>('local');
  const backendStatus = ref<Partial<BackendStatus>>({});
  const history = ref<HistoryItem[]>([]);
  const currentTaskId = ref<string | null>(null);
  const currentTaskSource = ref<'local' | 'cloud'>('local');
  const currentPrompt = ref('');
  const currentResultUrl = ref<string | null>(null);
  const error = ref<string | null>(null);

  // ---- 计算属性 ----
  const isGenerating = computed(() => state.value === 'generating');
  const isCompleted = computed(() => state.value === 'completed');
  const historyCount = computed(() => history.value.length);

  // ---- Actions ----

  /** 切换生成模式 */
  function setMode(m: GenerateMode) {
    mode.value = m;
    state.value = 'idle';
    currentTaskId.value = null;
    currentResultUrl.value = null;
    error.value = null;
  }

  /** 切换本地/云端模式 */
  function toggleCloud() {
    cloudMode.value = cloudMode.value === 'local' ? 'cloud' : 'local';
  }

  /** 更新后端状态（由 Tauri 事件触发） */
  function updateBackendStatus(s: Partial<BackendStatus>) {
    backendStatus.value = s;
  }

  /**
   * 发起生成请求
   * 参数映射：
   *   t2v  → { mode: 'txt2video', prompt }
   *   i2v  → { mode: 'img2video', prompt, image_path, params: { denoising_strength } }
   *   v2v  → { mode: 'video2video', prompt, image_path, params: { style } }
   *
   * 方案 B 流程（本地模式）：
   *   1. 调用 generate() 获取 GenerationController（包含 taskId）
   *   2. 将 taskId 暴露给 UI 层，由 useProgress.startTracking(taskId) 建立 WebSocket
   *   3. 注册 onDone / onError 回调处理终态
   *   4. 完成时通过 buildVideoUrl 将 output_path 转为 HTTP URL
   *
   * 云端模式（Task #11 集成）：
   *   1. 调用 cloudGenerate() → POST /api/v1/cloud/generate
   *   2. 若返回 auto_fallback=true，自动降级走本地 generate()
   *   3. 若返回 task_id 且 source='cloud'，用 WS 连接 /cloud/progress/ws?task_id=xxx
   *   4. 终态由 WS 消息推送触发
   */
  async function startGeneration(params: {
    prompt: string;
    mode: GenerateMode;
    imagePath?: string;
    motionStrength?: number; // 1-10
    style?: string;
  }) {
    // ---- 检查认证与额度 ----
    const authStore = useAuthStore();
    const check = authStore.canGenerate();
    if (!check.ok) {
      state.value = 'failed';
      error.value = check.reason || '无法生成';
      return;
    }

    error.value = null;
    currentPrompt.value = params.prompt;
    currentResultUrl.value = null;
    state.value = 'generating';

    // 映射为后端参数
    const backendParams: GenerateParams = {
      mode: params.mode === 't2v' ? 'txt2video'
            : params.mode === 'i2v' ? 'img2video'
            : 'video2video',
      prompt: params.prompt,
      image_path: params.imagePath,
      params: {},
    };
    if (params.motionStrength != null) {
      backendParams.params = { denoising_strength: params.motionStrength / 10 };
    }
    if (params.style) {
      backendParams.params = { ...(backendParams.params || {}), style: params.style };
    }

    // ---- 云端模式：先尝试云端，失败自动降级本地 ----
    if (cloudMode.value === 'cloud') {
      currentTaskSource.value = 'cloud';
      try {
        const cloudCtrl = await cloudGenerate(backendParams) as CloudGenerateController;
        const cloudRes = cloudCtrl as unknown as CloudGenerateResponse;

        // 后端指示自动降级
        if (cloudCtrl.auto_fallback) {
          currentTaskSource.value = 'local';
          error.value = '云端暂不可用，已自动切换为本地生成';
          await startLocalGeneration(backendParams, params);
          return;
        }

        // 云端提交成功，注册终态回调
        currentTaskId.value = cloudCtrl.task_id;
        cloudCtrl
          .onDone((p) => {
            handleDone(
              { task_id: p.task_id, state: 'completed', progress: 100, step: '', output_path: p.output_url || null, error: null },
              params,
              'cloud'
            );
          })
          .onError((p) => {
            state.value = 'failed';
            error.value = p.error || '云端生成失败';
          });
        return;
      } catch (e) {
        // 云端不可用，自动降级
        error.value = '云端连接失败，自动切换为本地生成';
        await startLocalGeneration(backendParams, params);
        return;
      }
    }

    // ---- 本地模式 ----
    await startLocalGeneration(backendParams, params);
  }

  /** 本地模式生成（统一封装） */
  async function startLocalGeneration(backendParams: GenerateParams, params: {
    prompt: string;
    mode: GenerateMode;
    imagePath?: string;
    motionStrength?: number;
    style?: string;
  }) {
    try {
      currentTaskSource.value = 'local';
      const ctrl = await generate(backendParams);
      currentTaskId.value = ctrl.taskId;

      ctrl
        .onDone((s) => handleDone(s, params, 'local'))
        .onError((s) => {
          state.value = 'failed';
          error.value = s.error || '生成失败，请重试';
        });
    } catch (e) {
      state.value = 'failed';
      error.value = `发起请求失败: ${e}`;
    }
  }

  /** 统一处理完成事件 */
  function handleDone(s: TaskStatus, params: { prompt: string }, source: 'local' | 'cloud') {
    state.value = 'completed';
    const authStore = useAuthStore();
    authStore.consumeQuota();
    authStore.refreshQuota();

    if (s.output_path) {
      const httpUrl = buildVideoUrl(s.output_path, s.task_id);
      currentResultUrl.value = httpUrl;
      const item: HistoryItem = {
        id: s.task_id,
        mode: mode.value,
        prompt: params.prompt,
        thumbnailUrl: '',
        videoUrl: httpUrl,
        resolution: '1280×720',
        durationSec: 4,
        renderMode: source === 'cloud' ? '云端加速' : '本地GPU',
        createdAt: new Date().toISOString(),
      };
      history.value.unshift(item);
      persistHistory();
    } else {
      currentResultUrl.value = null;
    }
  }

  /** 取消当前生成任务 */
  async function cancelGeneration() {
    if (!currentTaskId.value) return;
    try {
      const { cancelTask } = await import('../api/nexus');
      await cancelTask(currentTaskId.value);
    } catch { /* 忽略取消错误 */ }
    state.value = 'idle';
    currentTaskId.value = null;
    currentResultUrl.value = null;
  }

  /** 加载历史（从 localStorage 恢复） */
  function loadHistory() {
    try {
      const raw = localStorage.getItem('nexusvideo_history');
      if (raw) history.value = JSON.parse(raw);
    } catch { /* 静默失败 */ }
  }

  /** 持久化历史 */
  function persistHistory() {
    try {
      localStorage.setItem('nexusvideo_history', JSON.stringify(history.value));
    } catch { /* 存储满时静默 */ }
  }

  /** 清空历史 */
  function clearHistory() {
    history.value = [];
    persistHistory();
  }

  /** 删除单个历史项 */
  function removeHistory(id: string) {
    history.value = history.value.filter((h) => h.id !== id);
    persistHistory();
  }

  /** 加载后端状态 */
  async function refreshBackendStatus() {
    try {
      const s = await getBackendStatus();
      backendStatus.value = s;
    } catch {
      backendStatus.value = {};
    }
  }

  // ---- 返回 ----
  return {
    state, mode, cloudMode, backendStatus, history,
    currentTaskId, currentTaskSource, currentPrompt,
    currentResultUrl, error,
    isGenerating, isCompleted, historyCount,
    setMode, toggleCloud, updateBackendStatus,
    startGeneration, cancelGeneration,
    loadHistory, clearHistory, removeHistory,
    refreshBackendStatus,
  };
});