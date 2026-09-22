import { defineConfig } from "vite";

// The viewer is served from https://<user>.github.io/mtl-roofs/, so asset URLs
// must be relative to that sub-path rather than the domain root.
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    sourcemap: true,
    // 3D Tiles payloads are fetched at runtime from the release asset bundle,
    // never inlined, so the JS bundle stays small.
    assetsInlineLimit: 4096,
  },
});
