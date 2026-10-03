// Renders design.html layers to transparent PNGs:  node render.mjs
import { createRequire } from "module";
import path from "path";
import { fileURLToPath } from "url";

const require = createRequire(import.meta.url);
let pw;
try { pw = require("playwright"); } catch { pw = require("/opt/node-tools/node_modules/playwright-core"); }
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, "png");
const layers = ["back", "overlay", "fill", "cap", "glow", "marker", "petal", "preview"];

const browser = await pw.chromium.launch({
  executablePath: process.env.CHROME || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
});
const page = await browser.newPage({ deviceScaleFactor: 1 });
const fs = await import("fs");
fs.mkdirSync(out, { recursive: true });
for (const layer of layers) {
  await page.goto("file://" + path.join(here, "design.html") + "?layer=" + layer);
  await page.waitForFunction(() => window.READY);
  await page.evaluate(() => document.fonts.ready);
  const [w, h] = await page.evaluate(() => window.SIZE);
  await page.setViewportSize({ width: w, height: h });
  const file = path.join(out, `sakura_power_${layer}.png`);
  await page.screenshot({ path: file, omitBackground: layer !== "preview", clip: { x: 0, y: 0, width: w, height: h } });
  console.log("wrote", file, w + "x" + h);
}
await browser.close();
