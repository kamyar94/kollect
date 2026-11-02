// Right-click menu: send the links inside the highlighted text (or the link under the pointer) to Kollect.

if (typeof importScripts === "function") {
  importScripts("shared.js"); // Chrome's service worker; Firefox loads shared.js from the manifest
}

const MENU_ID = "kollect-selected-links";

function makeMenu() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_ID,
      title: "Send selected links to Kollect",
      contexts: ["selection", "link"],
    });
  });
}

chrome.runtime.onInstalled.addListener(makeMenu);
chrome.runtime.onStartup.addListener(makeMenu);

// Runs inside the page: the links that the highlighted text touches, plus addresses typed in it.
function linksInSelection() {
  const selection = window.getSelection();
  const found = [];
  if (selection && selection.rangeCount && !selection.isCollapsed) {
    for (const link of document.querySelectorAll("a[href], area[href]")) {
      if (selection.containsNode(link, true)) {
        found.push(link.href);
      }
    }
    for (const match of selection.toString().matchAll(/https?:\/\/[^\s<>"']+/g)) {
      found.push(match[0]);
    }
  }
  return found;
}

// Runs inside the page: copy text, keeping the user's highlight where it was.
async function copyInPage(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const selection = window.getSelection();
    const ranges = [];
    for (let i = 0; i < selection.rangeCount; i++) {
      ranges.push(selection.getRangeAt(i));
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.documentElement.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    selection.removeAllRanges();
    ranges.forEach((range) => selection.addRange(range));
    return copied;
  }
}

function runInPage(tabId, frameId, func, args) {
  return new Promise((resolve) => {
    chrome.scripting.executeScript({ target: { tabId, frameIds: [frameId] }, func, args: args || [] }, (results) => {
      resolve(chrome.runtime.lastError || !results || !results[0] ? null : results[0].result);
    });
  });
}

function showBadge(tabId, text, color, title) {
  chrome.action.setBadgeBackgroundColor({ tabId, color });
  chrome.action.setBadgeText({ tabId, text });
  chrome.action.setTitle({ tabId, title });
  setTimeout(() => {
    chrome.action.setBadgeText({ tabId, text: "" });
    chrome.action.setTitle({ tabId, title: "Send tabs to Kollect" });
  }, 6000);
}

async function handleMenuClick(info, tab) {
  if (info.menuItemId !== MENU_ID || !tab || tab.id === undefined) {
    return;
  }
  const frameId = info.frameId || 0;
  const selected = (await runInPage(tab.id, frameId, linksInSelection)) || [];
  const links = webLinks(selected.length ? selected : [info.linkUrl]);
  if (!links.length) {
    showBadge(tab.id, "0", "#888888", "No links in the highlighted text");
    return;
  }
  const copied = await runInPage(tab.id, frameId, copyInPage, [links.join("\n") + "\n"]);
  const problem = await sendToKollect(links);
  const count = String(links.length);
  if (problem) {
    showBadge(tab.id, "!", "#c01c28", `${copied ? "Copied" : "Found"} ${count} links. Kollect: ${problem}`);
  } else {
    showBadge(tab.id, count, "#26a269",
      `Sent ${count} links to Kollect${copied ? " and copied them" : " (could not copy them)"}`);
  }
}

chrome.contextMenus.onClicked.addListener(handleMenuClick);
