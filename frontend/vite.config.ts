import { defineConfig } from "vitest/config";

export default defineConfig({
  // A GitHub Pages project site serves from /<repo>/, not from /, so the
  // built asset URLs have to carry that prefix. scripts/publish_web.py sets
  // this alongside VITE_DATA_BASE_URL; a plain build stays rooted at /.
  base: process.env.VITE_BASE_PATH ?? "/",
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
