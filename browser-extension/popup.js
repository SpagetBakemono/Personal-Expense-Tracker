const APP_URL = "http://127.0.0.1:8000";

const accountSelect = document.getElementById("account");
const captureButton = document.getElementById("capture");
const statusEl = document.getElementById("status");

function setStatus(text, isError) {
  statusEl.textContent = text;
  statusEl.className = isError ? "error" : "";
}

function setStatusHtml(html) {
  statusEl.innerHTML = html;
  statusEl.className = "";
}

async function loadAccounts() {
  try {
    const res = await fetch(`${APP_URL}/api/accounts`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const accounts = await res.json();
    if (accounts.length === 0) {
      setStatus("No accounts found -- add one in the app first.", true);
      captureButton.disabled = true;
      return;
    }
    for (const account of accounts) {
      const option = document.createElement("option");
      option.value = String(account.id);
      // textContent, not innerHTML -- account names are untrusted data.
      option.textContent = account.name;
      accountSelect.appendChild(option);
    }
  } catch (err) {
    setStatus("Can't reach Expense Tracker -- is it running?", true);
    captureButton.disabled = true;
  }
}

async function openOrFocusReviewTab() {
  const reviewUrl = `${APP_URL}/import/review`;
  // host_permissions already covers APP_URL, which is what lets the url
  // filter here work without asking for the much broader "tabs"
  // permission (see manifest.json).
  const existing = await chrome.tabs.query({ url: `${APP_URL}/*` });

  if (existing.length > 0) {
    const tab = existing[0];
    await chrome.tabs.update(tab.id, { active: true, url: reviewUrl });
    if (tab.windowId !== undefined) {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  } else {
    await chrome.tabs.create({ url: reviewUrl });
  }
  window.close();
}

async function captureAndSend() {
  captureButton.disabled = true;
  setStatus("Reading page...");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("No active tab");

    const [{ result: pageText }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.body.innerText,
    });

    if (!pageText || !pageText.trim()) {
      throw new Error("Couldn't find any text on this page");
    }

    const formData = new FormData();
    formData.append("account_id", accountSelect.value);
    formData.append("statement_text", pageText);

    setStatus("Reading transactions...");
    const res = await fetch(`${APP_URL}/import/capture`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }

    let html =
      `Found ${data.count} transaction${data.count === 1 ? "" : "s"}. ` +
      `<a href="${APP_URL}/import/review" id="review-link">Review them &rarr;</a>`;

    if (typeof data.bank_balance === "number") {
      const fmt = (n) => `$${n.toFixed(2)}`;
      if (data.balance_matches) {
        html += `<div class="balance-check ok">&check; Balance matches: ${fmt(data.bank_balance)}</div>`;
      } else {
        html +=
          `<div class="balance-check mismatch">&#9888; Bank says ${fmt(data.bank_balance)}, ` +
          `app projects ${fmt(data.app_balance)} (off by ${fmt(Math.abs(data.difference))}) -- ` +
          `something may be missing or duplicated.</div>`;
      }
    }

    setStatusHtml(html);
    document.getElementById("review-link")?.addEventListener("click", (e) => {
      e.preventDefault();
      openOrFocusReviewTab();
    });
  } catch (err) {
    setStatus(`Couldn't do that: ${err.message}`, true);
  } finally {
    captureButton.disabled = false;
  }
}

captureButton.addEventListener("click", captureAndSend);
loadAccounts();
