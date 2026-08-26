<!--
 * SkillsView.vue — 内置技能 Gallery 页面
 * ============================================================
 * 来源：v0.2.10 修复任务 — 构建内置技能页面
 * 对接 client/src/api/skills.ts
 * 视觉：与 Text2VideoView 一致的毛玻璃深色主题
 * 交互：卡片网格 → 点击弹出"已选择，返回生成面板"提示
-->
<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { listSkills, type SkillMeta } from '../api/skills';

const skills = ref<SkillMeta[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

const modeLabel: Record<string, string> = {
  t2v: '文字',
  i2v: '图生',
  v2v: '风格',
};
const modeColor: Record<string, string> = {
  t2v: '#8B5CF6',
  i2v: '#60A5FA',
  v2v: '#22D3EE',
};
const categoryLabel: Record<string, string> = {
  general: '通用',
  landscape: '风景',
  character: '角色',
  product: '产品',
  cinematic: '影视',
  abstract: '抽象',
  style: '风格',
  motion: '运动',
};

async function loadSkills() {
  loading.value = true;
  error.value = null;
  try {
    skills.value = await listSkills();
  } catch (e) {
    error.value = (e as Error).message || '获取技能列表失败';
    skills.value = [];
  } finally {
    loading.value = false;
  }
}

function onCardClick(skill: SkillMeta) {
  alert(`技能「${skill.name}」已选择，返回生成面板创建任务`);
}

const titleEntered = ref(false);
onMounted(() => {
  loadSkills();
  setTimeout(() => { titleEntered.value = true; }, 300);
});
</script>

<template>
  <div class="skills-view">
    <div class="skills-header">
      <h1 class="skills-title" :class="{ 'btn-enter-fade': true, 'entered': titleEntered }">🧩 技能中心</h1>
      <p class="skills-subtitle" :class="{ 'btn-enter-fade': true, 'entered': titleEntered }">
        内置工作流一览 · 点击技能卡片返回生成面板创建任务
      </p>
    </div>

    <!-- 加载骨架屏 -->
    <div v-if="loading" class="skills-skeleton">
      <div class="skeleton-card" v-for="i in 6" :key="i">
        <div class="skeleton-line skeleton-line--short"></div>
        <div class="skeleton-line"></div>
        <div class="skeleton-line skeleton-line--mid"></div>
      </div>
    </div>

    <!-- 空状态 -->
    <div
      v-else-if="!error && skills.length === 0"
      class="skills-empty"
    >
      <div class="skills-empty__icon">🧩</div>
      <p class="skills-empty__title">暂无内置技能</p>
      <p class="skills-empty__sub">后端未提供内置技能，或技能中心暂未加载</p>
      <button class="btn-primary" @click="loadSkills()">刷新</button>
    </div>

    <!-- 加载错误 -->
    <div v-else-if="error" class="skills-error">
      <p class="skills-error__title">⚠️ 加载失败</p>
      <p class="skills-error__sub">{{ error }}</p>
      <button class="btn-primary" @click="loadSkills()">重试</button>
    </div>

    <!-- 技能卡片网格 -->
    <div v-else class="skills-grid" :class="{ entered: titleEntered }">
      <div
        v-for="skill in skills"
        :key="skill.id"
        class="skill-card"
        @click="onCardClick(skill)"
      >
        <!-- 顶部色条（按 mode 上色） -->
        <div
          class="skill-card__bar"
          :style="{ background: modeColor[skill.mode] || 'var(--accent-1)' }"
        ></div>

        <div class="skill-card__head">
          <span class="skill-card__name">{{ skill.name }}</span>
          <span
            class="skill-card__mode"
            :style="{ color: modeColor[skill.mode] || 'var(--accent-1)' }"
          >
            {{ modeLabel[skill.mode] || skill.mode }}
          </span>
        </div>

        <div class="skill-card__category">
          {{ categoryLabel[skill.category] || skill.category }}
        </div>

        <p class="skill-card__desc">{{ skill.description }}</p>

        <div class="skill-card__meta">
          <template v-if="skill.required_models && skill.required_models.length > 0">
            <span
              v-for="m in skill.required_models.slice(0, 3)"
              :key="m"
              class="skill-card__model-tag"
            >{{ m }}</span>
            <span
              v-if="skill.required_models.length > 3"
              class="skill-card__model-tag skill-card__model-tag--more"
            >+{{ skill.required_models.length - 3 }}</span>
          </template>
          <span
            v-if="skill.cloud"
            class="skill-card__cloud-tag"
          >☁️</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skills-view {
  width: 100%;
  height: 100%;
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  align-items: center;
  box-sizing: border-box;
  overflow-y: auto;
}

.skills-header {
  text-align: center;
  margin-bottom: 20px;
}

.skills-title {
  font-size: var(--text-h1, 28px);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 6px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 400ms var(--ease-out-expo, ease-out), transform 400ms var(--ease-out-expo, ease-out);
}
.skills-title.entered { opacity: 1; transform: translateY(0); }

.skills-subtitle {
  color: var(--text-tertiary);
  font-size: 13px;
  margin: 0;
  opacity: 0;
  transform: translateY(6px);
  transition: opacity 400ms var(--ease-out-expo, ease-out) 100ms, transform 400ms var(--ease-out-expo, ease-out) 100ms;
}
.skills-subtitle.entered { opacity: 1; transform: translateY(0); }

/* ---- 骨架屏 ---- */
.skills-skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
  width: 100%;
  max-width: 1100px;
  padding: 8px 0;
}
.skeleton-card {
  height: 160px;
  background: var(--glass-1, rgba(255,255,255,0.04));
  border: 1px solid var(--border, rgba(255,255,255,0.08));
  border-radius: var(--radius-md, 12px);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.skeleton-line {
  height: 12px;
  background: linear-gradient(90deg, rgba(255,255,255,0.04) 25%, rgba(255,255,255,0.08) 50%, rgba(255,255,255,0.04) 75%);
  background-size: 200% 100%;
  border-radius: 6px;
  animation: skeleton-shimmer 1.6s ease-in-out infinite;
}
.skeleton-line--short { width: 40%; }
.skeleton-line--mid { width: 70%; }
@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ---- 空状态 ---- */
.skills-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 60px 24px;
}
.skills-empty__icon { font-size: 48px; margin-bottom: 4px; }
.skills-empty__title {
  margin: 0;
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 500;
}
.skills-empty__sub {
  margin: 0;
  color: var(--text-tertiary);
  font-size: 13px;
}

/* ---- 错误状态 ---- */
.skills-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 40px 24px;
}
.skills-error__title {
  margin: 0;
  color: var(--error, #FF5B5B);
  font-size: 16px;
  font-weight: 500;
}
.skills-error__sub {
  margin: 0;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}

/* ---- 卡片网格 ---- */
.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px;
  width: 100%;
  max-width: 1100px;
  padding: 8px 0 16px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 500ms var(--ease-out-expo, ease-out) 200ms, transform 500ms var(--ease-out-expo, ease-out) 200ms;
}
.skills-grid.entered { opacity: 1; transform: translateY(0); }

.skill-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px 14px;
  background: var(--glass-1, rgba(255,255,255,0.04));
  border: 1px solid var(--border, rgba(255,255,255,0.08));
  border-radius: var(--radius-md, 12px);
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  min-height: 160px;
}
.skill-card:hover {
  transform: translateY(-3px);
  border-color: var(--border-strong, rgba(255,255,255,0.16));
  background: var(--glass-2, rgba(255,255,255,0.06));
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
}

.skill-card__bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  border-radius: 3px 3px 0 0;
}

.skill-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.skill-card__name {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 500;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-card__mode {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(255,255,255,0.06);
  flex-shrink: 0;
}

.skill-card__category {
  font-size: 10px;
  color: var(--text-disabled, rgba(255,255,255,0.35));
  letter-spacing: 0.06em;
}

.skill-card__desc {
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.skill-card__meta {
  margin-top: auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.skill-card__model-tag {
  font-size: 10px;
  color: var(--text-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
}
.skill-card__model-tag--more {
  color: var(--text-disabled);
  background: transparent;
  border: none;
}
.skill-card__cloud-tag {
  margin-left: auto;
  font-size: 12px;
  color: var(--accent-cyan, #22D3EE);
}
</style>
