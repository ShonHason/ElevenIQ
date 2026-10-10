import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// base "./": the static build works under any sub-path (GitHub Pages / Cloudflare Pages).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  test: { include: ["src/**/*.test.ts"] },
});
