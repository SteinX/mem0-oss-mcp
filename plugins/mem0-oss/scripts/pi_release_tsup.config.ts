import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts", "src/entry.ts"],
  format: ["esm"],
  target: "es2022",
  splitting: true,
  clean: true,
  minify: true,
  noExternal: [/^zod(?:\/|$)/],
  external: [/^node:/, /^@earendil-works\//, "typebox"],
});
