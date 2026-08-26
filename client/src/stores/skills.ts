/*
 * NexusVideo 技能中心 Store（Skill Gallery）
 * ============================================================
 * 负责：
 *   - 拉取后端技能清单 GET /skills（顶层路由，非 /api/skills）
 *   - 后端未就绪时回退本地 mock 数据做联调（useMock 标志）
 *   - 缩略图相对路径 → 完整 HTTP URL（buildSkillThumbUrl）
 *
 * 后端契约（程流深 / 主理人对齐）：
 *   SkillSummary = {
 *     id, name, category, description, thumbnail(相对路径),
 *     mode('t2v'|'i2v'|'v2v'), default_params(含 prompt),
 *     param_schema[], cloud(bool), enabled(bool), risk_tier
 *   }
 *   - category 枚举由后端给定，前端按通用字符串渲染、过滤 chips 动态生成
 *   - param_schema 前端参数→ComfyUI 节点字段映射，面板动态渲染的硬依赖
 *   - cloud 区分本地/云端，前端按 cloudMode 过滤
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { getApiBaseUrl } from '../api/utils';

export type SkillMode = 't2v' | 'i2v' | 'v2v';

export interface SkillParamOption {
  label: string;
  value: string | number;
}

export interface SkillParamSchema {
  key: string;
  label: string;
  type: 'select' | 'slider' | 'text' | 'image';
  default: unknown;
  options?: SkillParamOption[];
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
}

export interface SkillSummary {
  id: string;
  name: string;
  category: string;
  description: string;
  thumbnail: string; // 相对路径，如 skills/<id>/thumbnail.png
  mode: SkillMode;
  default_params: Record<string, unknown> & { prompt?: string };
  param_schema: SkillParamSchema[];
  cloud: boolean;
  enabled: boolean;
  risk_tier: string;
}

// ---------- Mock 数据（后端 GET /skills 就绪前用于联调） ----------
const MOCK_SKILLS: SkillSummary[] = [
  {
    id: 'cinematic-trailer',
    name: '电影级预告片',
    category: 'portrait',
    description: '一句话生成电影感大片，自动运镜与电影级调色',
    thumbnail: '',
    mode: 't2v',
    risk_tier: 'low',
    cloud: false,
    enabled: true,
    default_params: {
      prompt: '电影级预告片，黄昏城市天际线，缓慢推进镜头，电影感调色',
      motion_scale: 1.2,
      steps: 25,
      sampler: 'dpmpp_2m',
    },
    param_schema: [
      { key: 'motion_scale', label: '运动幅度', type: 'slider', default: 1.2, min: 0.5, max: 2, step: 0.1, hint: '值越大画面越动感' },
      { key: 'steps', label: '生成步数', type: 'slider', default: 25, min: 10, max: 40, step: 1 },
      { key: 'sampler', label: '采样器', type: 'select', default: 'dpmpp_2m',
        options: [
          { label: 'DPM++ 2M', value: 'dpmpp_2m' },
          { label: 'Euler', value: 'euler' },
          { label: 'UniPC', value: 'unipc' },
        ] },
    ],
  },
  {
    id: 'cyberpunk-neon',
    name: '赛博朋克霓虹',
    category: 'effects',
    description: '霓虹光影 + 雨水反射，瞬间赛博都市氛围',
    thumbnail: '',
    mode: 't2v',
    risk_tier: 'low',
    cloud: false,
    enabled: true,
    default_params: {
      prompt: '赛博朋克城市，霓虹灯牌，雨水街道反射，电影级光影',
      negative_prompt: '低质量，模糊',
      cfg_scale: 7.5,
    },
    param_schema: [
      { key: 'cfg_scale', label: '提示词遵循度', type: 'slider', default: 7.5, min: 1, max: 15, step: 0.5 },
      { key: 'negative_prompt', label: '负向提示词', type: 'text', default: '低质量，模糊' },
    ],
  },
  {
    id: 'anime-keyframe',
    name: '动漫关键帧',
    category: 'portrait',
    description: '日系动画风格关键帧，柔和线条与粉彩',
    thumbnail: '',
    mode: 't2v',
    risk_tier: 'low',
    cloud: true,
    enabled: true,
    default_params: {
      prompt: '动漫风格少女，樱花飘落，柔和粉彩，日系动画',
      style_strength: 0.8,
    },
    param_schema: [
      { key: 'style_strength', label: '风格强度', type: 'slider', default: 0.8, min: 0, max: 1, step: 0.05 },
    ],
  },
  {
    id: 'image-bring-life',
    name: '照片动起来',
    category: 'motion',
    description: '上传一张静态照片，AI 生成自然的镜头运动',
    thumbnail: '',
    mode: 'i2v',
    risk_tier: 'medium',
    cloud: false,
    enabled: true,
    default_params: {
      prompt: '缓慢推进，轻微呼吸感',
      motion_strength: 5,
    },
    param_schema: [
      { key: 'motion_strength', label: '运动强度', type: 'slider', default: 5, min: 1, max: 10, step: 1, hint: '1=温柔 10=激烈' },
    ],
  },
  {
    id: 'video-to-anime',
    name: '视频转动漫',
    category: 'style',
    description: '把实拍视频一键重绘为日系动画风',
    thumbnail: '',
    mode: 'v2v',
    risk_tier: 'medium',
    cloud: true,
    enabled: true,
    default_params: {
      prompt: '日系动画风格重绘',
      style: 'anime',
      strength: 0.65,
    },
    param_schema: [
      { key: 'style', label: '风格', type: 'select', default: 'anime',
        options: [
          { label: '日系动画', value: 'anime' },
          { label: '油画', value: 'oil' },
          { label: '水彩', value: 'watercolor' },
        ] },
      { key: 'strength', label: '重绘强度', type: 'slider', default: 0.65, min: 0.2, max: 1, step: 0.05 },
    ],
  },
];

/**
 * 缩略图相对路径 → 完整 HTTP URL
 * 后端给相对路径（skills/<id>/thumbnail.png），走后端静态路由；
 * 已是完整 URL 则原样返回；空则返回 null（组件用品牌渐变占位）。
 */
export function buildSkillThumbUrl(thumb?: string): string | null {
  if (!thumb) return null;
  if (/^https?:\/\//.test(thumb)) return thumb;
  const base = getApiBaseUrl();
  return `${base}/${thumb.replace(/^\//, '')}`;
}

export const useSkillsStore = defineStore('skills', () => {
  const skills = ref<SkillSummary[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const usingMock = ref(false);

  /** 动态分类列表（chips 过滤用） */
  const categories = computed(() => {
    const set = new Set<string>();
    skills.value.forEach((s) => set.add(s.category));
    return Array.from(set);
  });

  /** 拉取技能清单：真实接口优先，失败回退 mock */
  async function fetchSkills() {
    loading.value = true;
    error.value = null;
    try {
      const base = getApiBaseUrl();
      const res = await fetch(`${base}/skills`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) throw new Error(`加载技能失败 (${res.status})`);
      const data = (await res.json()) as SkillSummary[];
      skills.value = data.filter((s) => s.enabled);
      usingMock.value = false;
    } catch (e) {
      // 后端 Skill Registry 未就绪 → 用本地 mock 联调，不阻塞前端开发
      skills.value = MOCK_SKILLS.filter((s) => s.enabled);
      usingMock.value = true;
      console.warn('[skills] 真实接口不可用，使用 mock 数据联调', e);
    } finally {
      loading.value = false;
    }
  }

  return {
    skills,
    loading,
    error,
    usingMock,
    categories,
    fetchSkills,
    buildSkillThumbUrl,
  };
});
