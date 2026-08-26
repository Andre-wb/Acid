import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

// Tauri expects a fixed port and relative asset paths in production.
export default defineConfig({
  plugins: [solid()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // don't watch the scratch cargo project the backend writes to
      ignored: ["**/src-tauri/**", "**/scratch/**"],
    },
  },
  build: {
    target: "esnext",
    outDir: "dist",
  },
});
