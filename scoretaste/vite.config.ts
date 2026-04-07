import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Assets load correctly when the app is served under /guide/
export default defineConfig({
  plugins: [react()],
  base: "/guide/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
