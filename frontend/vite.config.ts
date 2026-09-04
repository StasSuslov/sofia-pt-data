import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    // public/data is a symlink to ../../data/sofia/web (outside frontend/);
    // Vite resolves symlinks and checks the real path against fs.allow.
    fs: { allow: [".."] },
  },
  test: {
    environment: "node",
  },
});
