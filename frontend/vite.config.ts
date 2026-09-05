import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    // public/data is a symlink to ../../data/sofia/web (outside frontend/);
    // Vite resolves symlinks and checks the real path against fs.allow, so
    // the allowed root must cover the resolved data dir, not the whole repo.
    fs: { allow: ["../data/sofia/web"] },
  },
  test: {
    environment: "node",
  },
});
