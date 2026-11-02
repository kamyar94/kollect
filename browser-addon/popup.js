// The toolbar panel: send the addresses of the tabs in this window, or in all windows, to Kollect.
// "All windows" means windows of the same kind: private (incognito) tabs are never mixed with normal ones.

const statusLine = document.getElementById("status");
let thisWindow = [];
let allWindows = [];

function addresses(windows) {
  return webLinks(windows.flatMap((w) => (w.tabs || []).map((tab) => tab.url)));
}

function plural(n) {
  return `${n} link${n === 1 ? "" : "s"}`;
}

async function send(links, button) {
  if (!links.length) {
    statusLine.textContent = "No web pages are open there.";
    return;
  }
  button.disabled = true;
  let copied = true;
  try {
    await navigator.clipboard.writeText(links.join("\n") + "\n");
  } catch (e) {
    copied = false;
  }
  const problem = await sendToKollect(links);
  button.disabled = false;
  if (problem) {
    statusLine.textContent = `${copied ? "Copied" : "Could not copy"} ${plural(links.length)}. ` +
      `Kollect could not be reached (${problem}). In Kollect, press Add to browsers.`;
    statusLine.className = "problem";
  } else {
    statusLine.textContent = `Sent ${plural(links.length)} to Kollect` +
      (copied ? " and copied them to the clipboard." : ". They could not be copied to the clipboard.");
    statusLine.className = "done";
  }
}

chrome.windows.getCurrent({ populate: true }, (current) => {
  chrome.windows.getAll({ populate: true, windowTypes: ["normal"] }, (windows) => {
    thisWindow = addresses([current]);
    allWindows = addresses(windows.filter((w) => w.incognito === current.incognito));
    document.getElementById("window-count").textContent = `(${thisWindow.length})`;
    document.getElementById("all-count").textContent = `(${allWindows.length})`;
  });
});

document.getElementById("window").addEventListener("click", (e) => send(thisWindow, e.currentTarget));
document.getElementById("all").addEventListener("click", (e) => send(allWindows, e.currentTarget));
