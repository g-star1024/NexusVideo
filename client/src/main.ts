/**
 * main.ts — NexusVideo Vue 3 应用入口
 * ============================================================
 * 加载：
 *   - Vue 3
 *   - Pinia 状态管理
 *   - Vue Router 路由
 *   - 设计 Token CSS（全局 :root 变量）
 *   - 组件样式库
 *   - 动效样式库
 * ============================================================
 * 苏璃光设计系统要求：所有动效通过 CSS class 控制，不引入 Lottie 依赖
 */
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';

// 引入苏璃光设计 Token 和组件/动效样式
import './assets/css/design-tokens.css';
import './assets/css/components.css';
import './assets/css/animations.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.mount('#app');