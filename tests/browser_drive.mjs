// Drive a Chrome-family browser with a throwaway profile through the DevTools protocol (Node 22 or newer):
// load the add-on, open some pages, press "All windows" in the add-on's panel, then run the right-click action
// on highlighted text in one page. Prints what the add-on reported and what reached the clipboard.
//   node tests/browser_drive.mjs <browser> <profile dir> <add-on dir> <selection page url> <page url>...
// KOLLECT_BROWSER_ARGS adds browser options (for example --ozone-platform=x11), KOLLECT_PANEL_SHOT saves a picture
// of the panel. The add-on folder must allow script access to the test pages (the right-click action normally gets that from
// the click itself), and Kollect's browser bridge must be registered for the profile.
import { execFileSync, spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [browserPath, profile, addonDir, selectionPage, ...pages] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// GNOME lets only the focused app read the Wayland clipboard; its X11 copy can be read by anyone (needs python3-tk)
const clipboard = () => {
  try {
    return execFileSync("python3", ["-c", "import tkinter; r = tkinter.Tk(); r.withdraw(); print(r.clipboard_get(), end='')"],
      { env: { ...process.env, DISPLAY: process.env.DISPLAY || ":0" } }).toString();
  } catch {
    return "(could not read the clipboard)";
  }
};

const browser = spawn(browserPath, [`--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
  "--remote-debugging-port=0", "--enable-unsafe-extension-debugging",
  ...(process.env.KOLLECT_BROWSER_ARGS || "").split(" ").filter(Boolean), "about:blank"], { stdio: "ignore" });

const portFile = join(profile, "DevToolsActivePort");
for (let i = 0; i < 100 && !existsSync(portFile); i++) await sleep(200);
const [port, path] = readFileSync(portFile, "utf8").trim().split("\n");
const socket = new WebSocket(`ws://127.0.0.1:${port}${path}`);
await new Promise((r) => socket.addEventListener("open", r));

let nextId = 1;
const waiting = new Map();
socket.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { resolve, reject } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  }
});
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = nextId++;
  waiting.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params, sessionId }));
});
const attach = async (targetId) => (await send("Target.attachToTarget", { targetId, flatten: true })).sessionId;
const evaluate = async (session, expression) =>
  (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, session)).result.value;

try {
  const { id } = await send("Extensions.loadUnpacked", { path: addonDir });
  console.log("add-on loaded, id", id);
  for (const url of [selectionPage, ...pages]) await send("Target.createTarget", { url });
  await sleep(2500);

  // the panel, opened as a page of its own
  const { targetId } = await send("Target.createTarget", { url: `chrome-extension://${id}/popup.html` });
  const panel = await attach(targetId);
  await send("Emulation.setFocusEmulationEnabled", { enabled: true }, panel);
  await send("Page.bringToFront", {}, panel);
  await sleep(1500);
  console.log("panel buttons:", await evaluate(panel,
    "[...document.querySelectorAll('button')].map(b => b.textContent.trim()).join(' | ')"));
  await evaluate(panel, "document.getElementById('all').click()");
  let status = "";
  for (let i = 0; i < 40 && !/Sent|Copied|Could/.test(status); i++) {
    await sleep(250);
    status = await evaluate(panel, "document.getElementById('status').textContent");
  }
  console.log("panel says:", status);
  if (process.env.KOLLECT_PANEL_SHOT) {  // a picture of the panel at its real size, for the README
    const height = await evaluate(panel, "Math.ceil(document.body.getBoundingClientRect().height)");
    await send("Emulation.setDeviceMetricsOverride", { width: 284, height, deviceScaleFactor: 1, mobile: false }, panel);
    const { data } = await send("Page.captureScreenshot", { format: "png" }, panel);
    writeFileSync(process.env.KOLLECT_PANEL_SHOT, Buffer.from(data, "base64"));
    console.log("panel picture saved");
  }
  console.log("clipboard after the panel:", JSON.stringify(clipboard()));
  console.log("allowed in incognito:", await evaluate(panel,
    "new Promise(r => chrome.extension.isAllowedIncognitoAccess(r))"));

  // the right-click action on highlighted text
  const targets = (await send("Target.getTargets")).targetInfos;
  const pageTarget = targets.find((t) => t.type === "page" && t.url === selectionPage);
  const page = await attach(pageTarget.targetId);
  await send("Emulation.setFocusEmulationEnabled", { enabled: true }, page);
  await send("Page.bringToFront", {}, page);
  console.log("highlighted:", await evaluate(page, `(() => {
    const links = document.querySelectorAll("a[href]");
    const range = document.createRange();
    range.setStartBefore(links[0]);
    range.setEndAfter(links[1]);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    return getSelection().toString().length + " characters over the first two links";
  })()`));
  const worker = targets.find((t) => t.type === "service_worker" && t.url.includes(id));
  const background = await attach(worker.targetId);
  console.log("right-click action badge:", await evaluate(background, `(async () => {
    const [tab] = await chrome.tabs.query({ url: ${JSON.stringify(selectionPage)} });
    await handleMenuClick({ menuItemId: "kollect-selected-links", frameId: 0 }, tab);
    return (await chrome.action.getBadgeText({ tabId: tab.id })) + " | " + (await chrome.action.getTitle({ tabId: tab.id }));
  })()`));
  console.log("clipboard after the right-click action:", JSON.stringify(clipboard()));
} catch (e) {
  console.log("FAILED:", e.message);
} finally {
  await send("Browser.close").catch(() => {});
  setTimeout(() => { browser.kill(); process.exit(0); }, 1500);
}
