// Сборка фронтенда: src/main.tsx -> dist/assets/app.js, стили и index.html.
// npm run build — один раз, npm run watch — пересборка при изменениях.
import * as esbuild from "esbuild";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const watch = process.argv.includes("--watch");
mkdirSync("dist/assets", { recursive: true });
// Версия в адресе файлов: браузер не возьмёт из кэша старую сборку
const v = Date.now().toString(36);
writeFileSync("dist/index.html", readFileSync("index.html", "utf8").replace(/\/assets\/app\.(js|css)"/g, `/assets/app.$1?v=${v}"`));

const options = {
  entryPoints: { app: "src/main.tsx" },
  bundle: true,
  outdir: "dist/assets",
  format: "esm",
  target: ["es2020"],
  jsx: "automatic",
  minify: !watch,
  sourcemap: watch,
  loader: { ".css": "css" },
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production") },
  logLevel: "info",
};

if (watch) {
  const ctx = await esbuild.context(options);
  await ctx.watch();
  console.log("Слежу за изменениями в src/ …");
} else {
  await esbuild.build(options);
}
