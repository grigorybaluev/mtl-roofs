import { defineConfig } from "vite";

// The viewer is served from https://<user>.github.io/mtl-roofs/, so asset URLs
// must be relative to that sub-path rather than the domain root.
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    sourcemap: true,
    // The scene is fetched at runtime from the release asset, never bundled.
    assetsInlineLimit: 4096,
    // three.js alone is ~550 kB minified; one chunk is fine for a single-page viewer
    chunkSizeWarningLimit: 700,
  },
});
