// Shared by the panel and the right-click menu: keep web addresses, hand them to Kollect.

const KOLLECT_HOST = "io.github.kamyar94.kollect";

// Web addresses only (no browser pages such as chrome:// or about:), each once, in order.
function webLinks(urls) {
  const seen = new Set();
  return urls.filter((url) => {
    if (typeof url !== "string" || !/^https?:\/\//i.test(url) || seen.has(url)) {
      return false;
    }
    seen.add(url);
    return true;
  });
}

// Kollect's browser bridge puts the links into Kollect's links box (and opens Kollect if it is closed).
// Resolves to null when it worked, or to a short reason when it did not.
function sendToKollect(links) {
  return new Promise((resolve) => {
    chrome.runtime.sendNativeMessage(KOLLECT_HOST, { action: "paste", links }, (reply) => {
      if (chrome.runtime.lastError) {
        resolve(chrome.runtime.lastError.message || "Kollect could not be reached");
      } else if (!reply || !reply.ok) {
        resolve((reply && reply.error) || "Kollect did not answer");
      } else {
        resolve(null);
      }
    });
  });
}
