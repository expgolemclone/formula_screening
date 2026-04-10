import express from "express";
import { connect } from "puppeteer-real-browser";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

// puppeteer-real-browser's targetcreated listener races with page.close() on
// the error path: its async page setup throws TargetCloseError against a page
// we've already closed, which Node 24 treats as a fatal unhandled rejection.
process.on("unhandledRejection", (reason) => {
  console.error("unhandledRejection:", reason?.message || reason);
});

const POOL_SIZE = parseInt(process.env.BROWSER_POOL_SIZE || "10", 10);
const PAGE_TIMEOUT = parseInt(process.env.BROWSER_PAGE_TIMEOUT || "30000", 10);
const IDLE_TIMEOUT = parseInt(process.env.BROWSER_IDLE_TIMEOUT || "300", 10) * 1000;
const BROWSER_HEADLESS = parseBoolean(process.env.BROWSER_HEADLESS, false);
const BROWSER_DISABLE_XVFB = parseBoolean(process.env.BROWSER_DISABLE_XVFB, false);
const CHALLENGE_POLL_INTERVAL_MS = parseInt(
  process.env.BROWSER_CHALLENGE_POLL_INTERVAL_MS || "250",
  10,
);
const CHALLENGE_CLEAR_STABLE_MS = parseInt(
  process.env.BROWSER_CHALLENGE_CLEAR_STABLE_MS || "1000",
  10,
);
const HAS_NATIVE_DISPLAY = Boolean(process.env.DISPLAY);
const HAS_XVFB = detectXvfb();

const CHALLENGE_TITLE_PATTERNS = [
  "just a moment...",
  "attention required!",
];

const CHALLENGE_BODY_PATTERNS = [
  "performing security verification",
  "verification successful",
  "checking your browser before accessing",
];

/** @typedef {{ browser: import('puppeteer-core').Browser, lastUsed: number }} BrowserEntry */

/** @type {Map<string, BrowserEntry>} proxy address -> browser instance */
const browserPool = new Map();

/** @type {Map<string, Promise<BrowserEntry>>} in-flight connect() calls */
const pendingConnections = new Map();
let xvfbFallbackWarned = false;

function parseBoolean(rawValue, defaultValue) {
  if (rawValue === undefined) {
    return defaultValue;
  }
  return rawValue.toLowerCase() === "true";
}

function poolKey(proxyAddr, username) {
  return `${proxyAddr}|${username || ""}`;
}

function detectXvfb() {
  if (HAS_NATIVE_DISPLAY) {
    return true;
  }
  const result = spawnSync("which", ["Xvfb"], { stdio: "ignore" });
  return result.status === 0;
}

function shouldForceHeadless() {
  return !BROWSER_DISABLE_XVFB && !HAS_NATIVE_DISPLAY && !HAS_XVFB;
}

function buildBrowserOptions(proxyAddr, proxyType, username, password) {
  const useHeadless = BROWSER_HEADLESS || shouldForceHeadless();
  if (!BROWSER_HEADLESS && shouldForceHeadless() && !xvfbFallbackWarned) {
    console.warn("Xvfb unavailable, starting browser in headless mode");
    xvfbFallbackWarned = true;
  }
  const options = {
    headless: useHeadless ? "new" : false,
    disableXvfb: useHeadless ? true : BROWSER_DISABLE_XVFB,
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
      const [host, portStr] = proxyAddr.split(":");
      options.proxy = { host, port: parseInt(portStr, 10) };
      if (username && password !== undefined) {
        options.proxy.username = username;
        options.proxy.password = password;
      }
    }
  }

  return options;
}

async function connectBrowser(options) {
  const { browser, page } = await connect(options);
  let probePage = null;
  try {
    probePage = await browser.newPage();
  } catch (error) {
    try {
      await browser.close();
    } catch {
      // Browser may already be disconnected.
    }
    throw error;
  } finally {
    if (probePage) {
      await probePage.close().catch(() => {});
    }
    await page.close().catch(() => {});
  }
  return { browser, lastUsed: Date.now() };
}

function shouldRetryHeadless(error, options) {
  void error;
  return options.headless === false && options.disableXvfb !== true;
}

async function launchBrowser(proxyAddr, proxyType, username, password) {
  const options = buildBrowserOptions(proxyAddr, proxyType, username, password);
  try {
    return await connectBrowser(options);
  } catch (error) {
    if (!shouldRetryHeadless(error, options)) {
      throw error;
    }

    console.warn("Xvfb unavailable, retrying browser in headless mode");
    const fallbackOptions = {
      ...options,
      headless: "new",
      disableXvfb: true,
    };
    return connectBrowser(fallbackOptions);
  }
}

async function getBrowser(proxyAddr, proxyType, username, password) {
  const key = poolKey(proxyAddr, username);
  const existing = browserPool.get(key);
  if (existing) {
    existing.lastUsed = Date.now();
    return existing;
  }

  const pending = pendingConnections.get(key);
  if (pending) {
    return pending;
  }

  if (browserPool.size >= POOL_SIZE) {
    let oldestKey = null;
    let oldestTime = Infinity;
    for (const [candidateKey, entry] of browserPool) {
      if (entry.lastUsed < oldestTime) {
        oldestTime = entry.lastUsed;
        oldestKey = candidateKey;
      }
    }
    if (oldestKey) {
      await closeBrowser(oldestKey);
    }
  }

  const promise = launchBrowser(proxyAddr, proxyType, username, password).then((entry) => {
    browserPool.set(key, entry);
    pendingConnections.delete(key);
    return entry;
  }).catch((error) => {
    pendingConnections.delete(key);
    throw error;
  });

  pendingConnections.set(key, promise);
  return promise;
}

async function closeBrowser(proxyAddr) {
  const entry = browserPool.get(proxyAddr);
  if (entry) {
    browserPool.delete(proxyAddr);
    try {
      await entry.browser.close();
    } catch {
      // Browser may already be disconnected.
    }
  }
}

async function closeAllBrowsers() {
  const closeTasks = [...browserPool.keys()].map((key) => closeBrowser(key));
  await Promise.allSettled(closeTasks);
}

setInterval(async () => {
  const now = Date.now();
  for (const [key, entry] of browserPool) {
    if (now - entry.lastUsed > IDLE_TIMEOUT) {
      await closeBrowser(key);
    }
  }
}, 30_000);

function normalizeText(text) {
  return text.replace(/\s+/g, " ").trim();
}

function remainingTimeout(deadline) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) {
    throw new Error("Timed out while waiting for the page to become ready");
  }
  return remaining;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function readPageState(page) {
  const url = page.url();
  const title = await page.title().catch(() => "");
  const bodyText = await page.evaluate(
    () => (document.body ? document.body.innerText : ""),
  ).catch(() => "");

  return {
    url: url.toLowerCase(),
    title: normalizeText(title).toLowerCase(),
    bodyText: normalizeText(bodyText).toLowerCase(),
  };
}

function isChallengeState(pageState) {
  if (pageState.url.includes("__cf_chl")) {
    return true;
  }
  if (pageState.url.includes("/cdn-cgi/challenge-platform/")) {
    return true;
  }
  if (CHALLENGE_TITLE_PATTERNS.some((pattern) => pageState.title.includes(pattern))) {
    return true;
  }
  return CHALLENGE_BODY_PATTERNS.some((pattern) => pageState.bodyText.includes(pattern));
}

async function waitForPageReady(page, deadline, url) {
  let clearedAt = null;
  while (true) {
    const pageState = await readPageState(page);
    if (isChallengeState(pageState)) {
      clearedAt = null;
    } else if (clearedAt === null) {
      clearedAt = Date.now();
    } else if (Date.now() - clearedAt >= CHALLENGE_CLEAR_STABLE_MS) {
      return;
    }

    if (deadline - Date.now() <= 0) {
      throw new Error(`Challenge did not clear before timeout for ${url}`);
    }
    await sleep(Math.min(CHALLENGE_POLL_INTERVAL_MS, remainingTimeout(deadline)));
  }
}

async function navigateWithChallengeWait(page, url, deadline, allowDownloadAbort = false) {
  try {
    await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: remainingTimeout(deadline),
    });
  } catch (error) {
    const message = String(error?.message || error);
    if (allowDownloadAbort && message.includes("net::ERR_ABORTED")) {
      return { downloadStarted: true };
    }
    throw error;
  }

  await waitForPageReady(page, deadline, url);
  return { downloadStarted: false };
}

async function waitForDownload(dir, filesBefore, timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    await sleep(Math.min(500, deadline - Date.now()));
    const current = readdirSync(dir);
    const newFiles = current.filter(
      (fileName) => !filesBefore.has(fileName) && !fileName.endsWith(".crdownload"),
    );
    const stillDownloading = current.some((fileName) => fileName.endsWith(".crdownload"));
    if (newFiles.length > 0 && !stillDownloading) {
      return join(dir, newFiles[0]);
    }
  }
  throw new Error(`Download timed out after ${timeout}ms`);
}

const app = express();
app.use(express.json({ limit: "1mb" }));

app.get("/health", (_req, res) => {
  res.json({ status: "ok", pool_size: browserPool.size });
});

app.post("/fetch", async (req, res) => {
  const { url, proxy, proxyType, proxyUsername, proxyPassword, timeout } = req.body;
  if (!url) {
    return res.status(400).json({ error: "url is required" });
  }

  const proxyAddr = proxy || "direct";
  const key = poolKey(proxyAddr, proxyUsername);
  const pageTimeout = timeout || PAGE_TIMEOUT;
  const deadline = Date.now() + pageTimeout;

  let page = null;
  try {
    const entry = await getBrowser(proxyAddr, proxyType, proxyUsername, proxyPassword);
    page = await entry.browser.newPage();

    if (proxyUsername && proxyPassword !== undefined) {
      await page.authenticate({ username: proxyUsername, password: proxyPassword });
    }

    await navigateWithChallengeWait(page, url, deadline);
    const html = await page.content();

    res.json({ html, status: 200 });
  } catch (error) {
    await closeBrowser(key);
    res.status(502).json({
      error: error.message,
      status: 502,
      html: null,
    });
  } finally {
    if (page) {
      try {
        await page.close();
      } catch {
        // Page may already be closed.
      }
    }
  }
});

app.post("/download", async (req, res) => {
  const { url, downloadDir, selector, proxy, proxyType, proxyUsername, proxyPassword, timeout } = req.body;
  if (!url || !downloadDir) {
    return res.status(400).json({ error: "url and downloadDir are required" });
  }

  const pageTimeout = timeout || PAGE_TIMEOUT;
  const proxyAddr = proxy || "direct";
  const key = poolKey(proxyAddr, proxyUsername);
  const deadline = Date.now() + pageTimeout;
  let page = null;

  try {
    if (!existsSync(downloadDir)) {
      mkdirSync(downloadDir, { recursive: true });
    }
    const filesBefore = new Set(readdirSync(downloadDir));

    const entry = await getBrowser(proxyAddr, proxyType, proxyUsername, proxyPassword);
    page = await entry.browser.newPage();

    if (proxyUsername && proxyPassword !== undefined) {
      await page.authenticate({ username: proxyUsername, password: proxyPassword });
    }

    const client = await page.createCDPSession();
    await client.send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: downloadDir,
    });

    const navigationResult = await navigateWithChallengeWait(
      page,
      url,
      deadline,
      selector == null,
    );
    if (selector) {
      if (navigationResult.downloadStarted) {
        throw new Error("Download started before selector interaction");
      }
      await page.waitForSelector(selector, { timeout: remainingTimeout(deadline) });
      await page.click(selector);
    }

    const filePath = await waitForDownload(downloadDir, filesBefore, remainingTimeout(deadline));
    res.json({ filePath, status: 200 });
  } catch (error) {
    await closeBrowser(key);
    res.status(502).json({ error: error.message, status: 502, filePath: null });
  } finally {
    if (page) {
      try {
        await page.close();
      } catch {
        // Page may already be closed.
      }
    }
  }
});

app.post("/shutdown", async (_req, res) => {
  res.json({ status: "shutting_down" });
  await closeAllBrowsers();
  process.exit(0);
});

const server = app.listen(0, "127.0.0.1", () => {
  const { port } = server.address();
  console.log(`BROWSER_SERVICE_PORT=${port}`);
});

for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, async () => {
    await closeAllBrowsers();
    server.close();
    process.exit(0);
  });
}
