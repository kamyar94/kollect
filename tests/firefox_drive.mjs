// Drive Firefox with a throwaway profile through WebDriver BiDi (Node 22 or newer): install the add-on as a
// temporary add-on, open some pages, press "All windows" in the add-on's panel, then run the right-click action on
// highlighted text. Prints what the add-on reported and what reached the clipboard.
//   node tests/firefox_drive.mjs <profile dir> <add-on .xpi> <selection page url> <page url>...
// Test build of the add-on: host permission for the test pages (the right-click action normally gets it from the
// click itself) and a line in background.js that opens popup.html as a tab. The profile's user.js must grant host
// permissions by default (extensions.originControls.grantByDefault).
import { execFileSync, spawn } from "node:child_process";

const [profile, xpi, selectionPage, ...pages] = process.argv.slice(2);
const PORT = 9334;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clipboard = () => {
  try {
    return execFileSync("python3", ["-c", "import tkinter; r = tkinter.Tk(); r.withdraw(); print(r.clipboard_get(), end='')"],
      { env: { ...process.env, DISPLAY: process.env.DISPLAY || ":0" } }).toString();
  } catch {
    return "(could not read the clipboard)";
  }
};

const firefox = spawn("firefox", ["--profile", profile, "--no-remote", "--remote-debugging-port", String(PORT),
  "about:blank"], { stdio: "ignore", env: { ...process.env, MOZ_REMOTE_ALLOW_SYSTEM_ACCESS: "1" } });

let socket;
for (let i = 0; i < 60 && !socket; i++) {
  await sleep(1000);
  try {
    const s = new WebSocket(`ws://127.0.0.1:${PORT}/session`);
    await new Promise((resolve, reject) => { s.addEventListener("open", resolve); s.addEventListener("error", reject); });
    socket = s;
  } catch { /* not ready yet */ }
}
let nextId = 1;
const waiting = new Map();
socket.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { resolve, reject } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.type === "error" ? reject(new Error(`${msg.error}: ${msg.message}`)) : resolve(msg.result);
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  waiting.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});
const open = async (url) => {
  const { context } = await send("browsingContext.create", { type: "tab" });
  await send("browsingContext.navigate", { context, url, wait: "complete" });
  return context;
};
const evaluate = async (context, expression) => {
  const r = await send("script.evaluate", { expression, target: { context }, awaitPromise: true });
  return r.type === "exception" ? `exception: ${r.exceptionDetails.text}` : r.result.value;
};

try {
  await send("session.new", { capabilities: {} });
  for (const url of [selectionPage, ...pages]) await open(url);  // before the add-on, whose panel opens at once
  const { extension } = await send("webExtension.install", { extensionData: { type: "archivePath", path: xpi } });
  console.log("add-on installed (temporary):", extension);

  let panel;  // the test build of the add-on opens its panel as a tab (remote control may not open add-on pages)
  for (let i = 0; i < 40 && !panel; i++) {
    await sleep(250);
    const found = (await send("browsingContext.getTree")).contexts.find((c) => c.url.endsWith("/popup.html"));
    panel = found && found.context;
  }
  await sleep(1000);
  console.log("panel buttons:", await evaluate(panel,
    "[...document.querySelectorAll('button')].map(b => b.textContent.trim()).join(' | ')"));
  await evaluate(panel, "document.getElementById('all').click()");
  let status = "";
  for (let i = 0; i < 120 && !/Sent|Copied|Could/.test(status); i++) {
    await sleep(250);
    status = await evaluate(panel, "document.getElementById('status').textContent");
  }
  console.log("panel says:", status);
  console.log("clipboard after the panel:", JSON.stringify(clipboard()));
  console.log("allowed in private windows:", await evaluate(panel, "browser.extension.isAllowedIncognitoAccess()"));

  const tabs = (await send("browsingContext.getTree")).contexts;
  const page = tabs.find((c) => c.url === selectionPage).context;
  console.log("highlighted:", await evaluate(page, `(() => {
    const links = document.querySelectorAll("a[href]");
    const range = document.createRange();
    range.setStartBefore(links[0]);
    range.setEndAfter(links[1]);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    return "the first two links";
  })()`));
  console.log("right-click action badge:", await evaluate(panel, `(async () => {
    const background = await browser.runtime.getBackgroundPage();
    const tab = (await browser.tabs.query({})).find((t) => t.url === ${JSON.stringify(selectionPage)});
    await background.handleMenuClick({ menuItemId: "kollect-selected-links", frameId: 0 }, tab);
    return (await browser.action.getBadgeText({ tabId: tab.id })) + " | " + (await browser.action.getTitle({ tabId: tab.id }));
  })()`));
  console.log("clipboard after the right-click action:", JSON.stringify(clipboard()));
} catch (e) {
  console.log("FAILED:", e.message);
} finally {
  await send("browser.close").catch(() => {});
  setTimeout(() => { firefox.kill(); process.exit(0); }, 2000);
}
