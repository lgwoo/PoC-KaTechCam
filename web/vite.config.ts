import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 파이프라인 페이지가 server/ 의 파이썬 소스를 ?raw 로 읽어 코드 조각을 보여준다.
    // 손으로 베껴 두면 코드를 고쳤을 때 페이지가 조용히 옛 내용을 보여준다.
    fs: { allow: [".."] },
    // 프론트에서 /api 를 그대로 부르면 백엔드로 넘긴다 — CORS 를 신경 쓸 일이 줄어든다.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
