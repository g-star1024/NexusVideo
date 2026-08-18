import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Vite 配置：Tauri 推荐的固定端口 + HMR
export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    host: "127.0.0.1",
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "es2021",
    minify: "esbuild",
    sourcemap: false,
  },
});
