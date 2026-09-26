"use strict";
const $ = (id) => document.getElementById(id);
let browsers = [];
let current = null;
let health = {};

function visibleBrowsers() {
  const query = $("search").value.trim().toLowerCase();
  return browsers.filter((browser) =>
    (!$("worker").value || String(browser.worker) === $("worker").value) &&
    (`browser ${browser.id} ${browser.proxy_host}`.toLowerCase().includes(query)));
}

function renderList() {
  const visible = visibleBrowsers();
  $("count").textContent = `${visible.length} / ${browsers.length}`;
  $("browsers").replaceChildren();
  for (const browser of visible) {
    const button = document.createElement("button");
    button.className = "browser";
    button.setAttribute("aria-current", String(current?.id === browser.id));
    const name = document.createElement("strong");
    name.textContent = `Browser ${browser.id}`;
    const instance = document.createElement("span");
    instance.className = "instance";
    instance.textContent = `Host ${browser.worker}`;
    const proxy = document.createElement("small");
    proxy.textContent = `${browser.proxy_host}:${browser.proxy_port}`;
    button.append(name, instance, proxy);
    button.addEventListener("click", () => selectBrowser(browser));
    $("browsers").append(button);
  }
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "no-results";
    empty.textContent = "No browsers match. Try another number or clear the filters.";
    $("browsers").append(empty);
  }
  const index = visible.findIndex((browser) => browser.id === current?.id);
  $("previous").disabled = index <= 0;
  $("next").disabled = index < 0 || index >= visible.length - 1;
}

function updateStatus() {
  if (!current) return;
  const reachable = health[current.worker];
  $("notice").hidden = reachable !== false;
  $("notice").textContent = `Host ${current.worker} is not reachable yet. Initial setup can take several minutes. Check browser-fleet status; reconnect once the host is ready.`;
  $("connection").textContent = reachable === true ? `Host ${current.worker} viewer reachable · desktop status shown above` : `Checking host ${current.worker}…`;
}

function selectBrowser(browser) {
  current = browser;
  $("empty").hidden = true;
  $("desktop").hidden = false;
  $("desktop").src = browser.url;
  $("desktop").title = `Browser ${browser.id} remote desktop`;
  $("title").textContent = `Browser ${browser.id}`;
  $("details").textContent = `Host ${browser.worker} · ${browser.proxy_host}:${browser.proxy_port} · ${browser.protocol.toUpperCase()}`;
  $("new-tab").href = browser.url;
  $("new-tab").setAttribute("aria-disabled", "false");
  $("reconnect").disabled = false;
  renderList();
  updateStatus();
}

$("search").addEventListener("input", renderList);
$("worker").addEventListener("change", renderList);
$("reconnect").addEventListener("click", () => { if (current) selectBrowser(current); });
for (const [id, delta] of [["previous", -1], ["next", 1]]) {
  $(id).addEventListener("click", () => {
    const visible = visibleBrowsers();
    const index = visible.findIndex((browser) => browser.id === current?.id);
    if (visible[index + delta]) selectBrowser(visible[index + delta]);
  });
}

async function refreshHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error("Unavailable");
    health = await response.json();
    updateStatus();
  } catch {
    $("notice").hidden = false;
    $("notice").textContent = "The local connection manager is unavailable. Run browser-fleet open again.";
  } finally {
    setTimeout(refreshHealth, 10000);
  }
}

async function init() {
  try {
    const response = await fetch("/fleet.json");
    if (!response.ok) throw new Error("Unavailable");
    const fleet = await response.json();
    browsers = fleet.browsers;
    $("fleet-name").textContent = fleet.name;
    for (const worker of [...new Set(browsers.map((browser) => browser.worker))]) {
      const option = document.createElement("option");
      option.value = worker;
      option.textContent = `Host ${worker}`;
      $("worker").append(option);
    }
    renderList();
    refreshHealth();
  } catch {
    $("notice").hidden = false;
    $("notice").textContent = "Could not load your fleet. Restart browser-fleet open and reload this page.";
  }
}
init();
