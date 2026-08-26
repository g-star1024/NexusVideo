/**
 * skills.ts — 内置技能 Gallery API
 * ============================================================
 * 对接后端 /skills 系列端点
 *   GET    /skills              → 技能列表
 *   GET    /skills/{id}         → 技能详情 (manifest)
 *   POST   /skills/{id}/generate → 基于技能发起生成
 */
import { getApiBaseUrl } from './utils';

export interface SkillMeta {
  id: string;
  name: string;
  category: string;
  description: string;
  mode: 't2v' | 'i2v' | 'v2v';
  risk_tier: string;
  cloud: boolean;
  thumbnail?: string;
  required_models: string[];
  default_params?: Record<string, unknown>;
}

export interface SkillManifest extends SkillMeta {
  entry: string;
  enabled: boolean;
  workflow?: Record<string, unknown>;
  param_schema?: Record<string, unknown>;
}

const BASE = getApiBaseUrl();

export async function listSkills(): Promise<SkillMeta[]> {
  const res = await fetch(`${BASE}/skills`);
  if (!res.ok) throw new Error(`获取技能列表失败 (${res.status})`);
  return res.json() as Promise<SkillMeta[]>;
}

export async function getSkill(id: string): Promise<SkillManifest> {
  const res = await fetch(`${BASE}/skills/${id}`);
  if (!res.ok) throw new Error(`获取技能失败 (${res.status})`);
  return res.json() as Promise<SkillManifest>;
}

export async function generateSkill(
  id: string,
  prompt: string,
  params?: Record<string, unknown>
): Promise<{ task_id: string; seed: number; status: string; skill_id: string }> {
  const res = await fetch(`${BASE}/skills/${id}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, params }),
  });
  if (!res.ok) throw new Error(`技能生成失败 (${res.status})`);
  return res.json();
}
