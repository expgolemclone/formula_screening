import express from "express";
import { connect } from "puppeteer-real-browser";
import { readdirSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

// Configuration from environment or defaults (Python passes these via env vars)
const POOL_SIZE = parseInt(process.env.BROWSER_POOL_SIZE || "10", 10);
const PAGE_TIMEOUT = parseInt(process.env.BROWSER_PAGE_TIMEOUT || "30000", 10);
const IDLE_TIMEOUT = parseInt(process.env.BROWSER_IDLE_TIMEOUT || "300", 10) * 1000;

/** @typedef {{ browser: import('puppeteer-core').Browser, lastUsed: number }} BrowserEntry */

/** @type {Map<string, BrowserEntry>} proxy address -> browser instance */
const browserPool = new Map();

/** @type {Map<string, Promise<BrowserEntry>>} in-flight connect() calls */
const pendingConnections = new Map();

async function launchBrowser(proxyAddr, proxyType) {
  const options = {
    headless: "new",
    turnstile: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--ignore-certificate-errors",
    ],
  };

  const isDirect = !proxyAddr || proxyAddr.startsWith("direct");
  if (!isDirect) {
    if (proxyType === "socks5") {
      options.args.push(`--proxy-server=socks5://${proxyAddr}`);
    } else {
      const [host, port] = proxyAddr.split(":");
      options.proxy = { host, port };
    }
  }

  const { browser, page } = await connect(options);
  // Close the initial page that connect() opens — each request creates its own
  await page.close();
  return { browser, lastUsed: Date.now() };
}

async function getBrowser(proxyAddr, proxyType) {
  const existing = browserPool.get(proxyAddr);
  if (existing) {
    existing.lastUsed = Date.now();
    return existing;
  }

  // Deduplicate concurrent connect() calls for the same proxy
  const pending = pendingConnections.get(proxyAddr);
  if (pending) {
    return pending;
  }

  // Evict least-recently-used entry if pool is full
  if (browserPool.size >= POOL_SIZE) {
    let oldestKey = null;
    let oldestTime = Infinity;
    for (const [key, entry] of browserPool) {
      if (entry.lastUsed < oldestTime) {
        oldestTime = entry.lastUsed;
        oldestKey = key;
      }
    }
    if (oldestKey) {
      await closeBrowser(oldestKey);
    }
  }

  const promise = launchBrowser(proxyAddr, proxyType).then((entry) => {
    browserPool.set(proxyAddr, entry);
    pendingConnections.delete(proxyAddr);
    return entry;
  }).catch((err) => {
    pendingConnections.delete(proxyAddr);
    throw err;
  });

  pendingConnections.set(proxyAddr, promise);
  return promise;
}

async function closeBrowser(proxyAddr) {
  const entry = browserPool.get(proxyAddr);
  if (entry) {
    browserPool.delete(proxyAddr);
    try {
      await entry.browser.close();
    } catch {
      // Browser may already be disconnected
    }
  }
}

async function closeAllBrowsers() {
  const closeTasks = [...browserPool.keys()].map((key) => closeBrowser(key));
  await Promise.allSettled(closeTasks);
}

// Evict idle browsers periodically
setInterval(async () => {
  const now = Date.now();
  for (const [key, entry] of browserPool) {
    if (now - entry.lastUsed > IDLE_TIMEOUT) {
      await closeBrowser(key);
    }
  }
}, 30_000);

const app = express();
app.use(express.json({ limit: "1mb" }));

app.get("/health", (_req, res) => {
  res.json({ status: "ok", pool_size: browserPool.size });
});

app.post("/fetch", async (req, res) => {
  const { url, proxy, proxyType, timeout } = req.body;
  if (!url) {
    return res.status(400).json({ error: "url is required" });
  }

  const proxyKey = proxy || "direct";
  const pageTimeout = timeout || PAGE_TIMEOUT;

  let page = null;
  try {
    const entry = await getBrowser(proxyKey, proxyType);
    page = await entry.browser.newPage();

    await page.goto(url, { waitUntil: "networkidle2", timeout: pageTimeout });
    const html = await page.content();

    res.json({ html, status: 200 });
  } catch (err) {
    // On navigation failure, evict the browser so next request gets a fresh one
    await closeBrowser(proxyKey);
    res.status(502).json({
      error: err.message,
      status: 502,
      html: null,
    });
  } finally {
    if (page) {
      try { await page.close(); } catch { /* already closed */ }
    }
  }
});

app.post("/download", async (req, res) => {
  const { url, downloadDir, selector, proxy, proxyType, timeout } = req.body;
  if (!url || !downloadDir) {
    return res.status(400).json({ error: "url and downloadDir are required" });
  }

  const pageTimeout = timeout || PAGE_TIMEOUT;
  const proxyKey = proxy || "direct";
  let page = null;

  try {
    if (!existsSync(downloadDir)) mkdirSync(downloadDir, { recursive: true });
    const filesBefore = new Set(readdirSync(downloadDir));

    const entry = await getBrowser(proxyKey, proxyType);
    page = await entry.browser.newPage();

    const client = await page.createCDPSession();
    await client.send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: downloadDir,
    });

    // Navigation to a download URL triggers ERR_ABORTED — that is expected
    try {
      await page.goto(url, { waitUntil: "networkidle2", timeout: pageTimeout });
    } catch (navErr) {
      if (!navErr.message.includes("net::ERR_ABORTED")) {
        throw navErr;
      }
    }

    if (selector) {
      await page.waitForSelector(selector, { timeout: pageTimeout });
      await page.click(selector);
    }

    // Poll until a new non-temp file appears in downloadDir
    const filePath = await waitForDownload(downloadDir, filesBefore, pageTimeout);
    res.json({ filePath, status: 200 });
  } catch (err) {
    await closeBrowser(proxyKey);
    res.status(502).json({ error: err.message, status: 502, filePath: null });
  } finally {
    if (page) {
      try { await page.close(); } catch { /* already closed */ }
    }
  }
});

async function waitForDownload(dir, filesBefore, timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 500));
    const current = readdirSync(dir);
    const newFiles = current.filter(
      (f) => !filesBefore.has(f) && !f.endsWith(".crdownload"),
    );
    const stillDownloading = current.some((f) => f.endsWith(".crdownload"));
    if (newFiles.length > 0 && !stillDownloading) {
      return join(dir, newFiles[0]);
    }
  }
  throw new Error(`Download timed out after ${timeout}ms`);
}

app.post("/shutdown", async (_req, res) => {
  res.json({ status: "shutting_down" });
  await closeAllBrowsers();
  process.exit(0);
});

// Listen on OS-assigned port; print it for the Python parent to read
const server = app.listen(0, "127.0.0.1", () => {
  const { port } = server.address();
  // This line is parsed by BrowserService.start() in Python
  console.log(`BROWSER_SERVICE_PORT=${port}`);
});

// Graceful shutdown on SIGTERM/SIGINT
for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, async () => {
    await closeAllBrowsers();
    server.close();
    process.exit(0);
  });
}
