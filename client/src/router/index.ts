/*
 * NexusVideo Vue Router
 * ============================================================
 * 来源：苏璃光高保真规格书 — 三大模式切换 + 用户认证
 * 路由设计：
 *   /          → 一句话出片（默认）
 *   /img2video → 图生视频
 *   /style     → 视频风格化
 *   /login     → 登录/注册
 *
 * 路由守卫：
 *   1. 未登录且访问非登录页 → 重定向到 /login
 *   2. 已登录访问 /login    → 重定向到 /
 *   3. 页面切换使用 Vue Transition + page-fade 类名
 */
import { createRouter, createWebHashHistory } from 'vue-router';
import { getAuthToken } from '../api/nexus';

const router = createRouter({
  history: createWebHashHistory(), // Tauri 沙盒下用 hash 模式最稳妥
  routes: [
    {
      path: '/',
      name: 'text2video',
      component: () => import('../views/Text2VideoView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/img2video',
      name: 'img2video',
      component: () => import('../views/Img2VideoView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/style',
      name: 'style',
      component: () => import('../views/StyleTransferView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('../pages/SettingsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { requiresAuth: false },
    },
  ],
});

// ---- 路由守卫 ----
router.beforeEach((to, _from, next) => {
  const token = getAuthToken();
  const isAuthenticated = !!token;
  const requiresAuth = to.meta.requiresAuth !== false;

  if (requiresAuth && !isAuthenticated) {
    // 未登录且访问需要认证的页面 → 跳转到登录页
    next({ name: 'login' });
  } else if (!requiresAuth && isAuthenticated && to.name === 'login') {
    // 已登录访问登录页 → 跳转到主界面
    next({ name: 'text2video' });
  } else {
    next();
  }
});

export default router;