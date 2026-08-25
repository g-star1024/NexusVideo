<!--
 * AppSidebar.vue — 左侧历史栏
 * ============================================================
 * 来源：苏璃光高保真规格书 1.2 节
 * 尺寸：200×828，背景 --bg-surface
 * 内容：顶部标题 + 搜索框 + 历史列表 + 底部计数
 * 列表项：128×72 缩略图，hover 有 border + shadow + translateY(-2px)
 * 选中态：border --border-brand + 左侧 3px 渐变竖条
-->
<script setup lang="ts">
import { ref } from 'vue';
import { useGenerateStore } from '../stores/generate';

const store = useGenerateStore();
const selectedId = ref<string | null>(null);
const searchQuery = ref('');

// 过滤后的历史列表
function filteredHistory() {
  if (!searchQuery.value) return store.history;
  const q = searchQuery.value.toLowerCase();
  return store.history.filter((h) => h.prompt.toLowerCase().includes(q));
}
</script>

<template>
  <aside class="sidebar">
    <!-- 顶部标题区 -->
    <div class="sidebar__header">
      <span class="sidebar__title">历史记录</span>
      <button
        v-if="store.historyCount > 0"
        class="sidebar__clear-btn"
        title="清空历史"
        @click="store.clearHistory()"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path d="M5.5 5.5a1 1 0 0 0 0 1.4l2.5 2.5-2.5 2.5a1 1 0 0 0 1.4 1.4l2.5-2.5 2.5 2.5a1 1 0 1 0 1.4-1.4L10.9 9l2.5-2.5a1 1 0 0 0-1.4-1.4L9.5 7.6 7 5.1a1 1 0 0 0-1.4 1.4z" transform="translate(1 2)"/>
        </svg>
      </button>
    </div>

    <!-- 搜索框 -->
    <div class="sidebar__search">
      <svg
        class="sidebar__search-icon"
        width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"
      >
        <circle cx="7" cy="7" r="4"/>
        <line x1="10" y1="10" x2="14" y2="14"/>
      </svg>
      <input
        v-model="searchQuery"
        class="input-search"
        type="text"
        placeholder="搜索历史"
      />
    </div>

    <!-- 历史列表 -->
    <div class="sidebar__list">
      <template v-if="filteredHistory().length === 0">
        <div class="sidebar__empty">
          <span>暂无历史记录</span>
          <span class="sidebar__empty-hint">生成的视频将出现在这里</span>
        </div>
      </template>
      <template v-else>
        <div
          v-for="item in filteredHistory()"
          :key="item.id"
          class="history-card"
          :class="{ selected: selectedId === item.id }"
          @click="selectedId = selectedId === item.id ? null : item.id"
        >
          <!-- 渐变竖条（选中态） -->
          <div class="history-card__selected-bar"></div>

          <!-- 缩略图占位（实际接入后端视频首帧） -->
          <div class="history-card__thumb-placeholder">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4">
              <polygon points="6,4 12,8 6,12" fill="currentColor"/>
            </svg>
          </div>

          <!-- 播放悬浮覆盖 -->
          <div class="history-card__play-overlay">
            <svg width="20" height="20" viewBox="0 0 16 16" fill="currentColor">
              <polygon points="5,3 13,8 5,13"/>
            </svg>
          </div>

          <span class="history-card__mode-tag">{{
            item.mode === 't2v' ? '文字' : item.mode === 'i2v' ? '图生' : '风格'
          }}</span>
        </div>
      </template>
    </div>

    <!-- 底部计数 -->
    <div class="sidebar__footer">
      共 {{ store.historyCount }} 个作品
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 100%;
  height: 100%;
  min-height: 0;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sidebar__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.sidebar__title {
  color: var(--text-tertiary);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.05em;
}
.sidebar__clear-btn {
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color var(--dur-hover) var(--ease-in-out-cubic);
}
.sidebar__clear-btn:hover { color: var(--text-primary); }

.sidebar__search {
  position: relative;
}
.sidebar__search-icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-tertiary);
  pointer-events: none;
}

.sidebar__list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
  padding-right: 4px;
}

.sidebar__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px 12px;
  color: var(--text-tertiary);
  font-size: 13px;
}
.sidebar__empty-hint {
  font-size: 11px;
  color: var(--text-disabled);
}

.history-card__thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-1);
  color: var(--text-tertiary);
}
.history-card__mode-tag {
  position: absolute;
  bottom: 4px;
  left: 6px;
  font-size: 9px;
  color: var(--text-tertiary);
  background: rgba(0,0,0,0.5);
  padding: 1px 4px;
  border-radius: 3px;
}

.sidebar__footer {
  padding-top: 8px;
  border-top: var(--divider);
  text-align: center;
  color: var(--text-disabled);
  font-size: 11px;
}
</style>