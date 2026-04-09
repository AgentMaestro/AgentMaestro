(() => {
  const shell = document.querySelector("[data-finance-chat]");
  if (!shell) {
    return;
  }

  const wsUrl = (shell.dataset.financeWsUrl || "").trim();
  const agentSlug = (shell.dataset.financeAgentSlug || "").trim();
  const refreshUrl = (shell.dataset.financeRefreshUrl || "").trim();
  const defaultAutoFetch = String(shell.dataset.financeAutoFetchDefault || "true").trim() !== "false";
  const statusEl = shell.querySelector("[data-finance-status]");
  const messagesEl = shell.querySelector("[data-finance-messages]");
  const form = shell.querySelector("[data-finance-form]");
  const inputEl = shell.querySelector("[data-finance-input]");
  const noteEl = shell.querySelector("[data-finance-note]");
  const autoFetchToggle = shell.querySelector("[data-finance-auto-fetch-toggle]");
  const refreshButton = shell.querySelector("[data-finance-refresh-now]");
  const searchInputEl = shell.ownerDocument.querySelector("[data-finance-ticker-search]");
  const searchResultsEl = shell.ownerDocument.querySelector("[data-finance-ticker-results]");
  const searchUrl = (shell.dataset.financeSearchUrl || "").trim();
  const researchUrl = (shell.dataset.financeResearchUrl || "").trim();
  const tabButtons = Array.from(shell.ownerDocument.querySelectorAll("[data-finance-tab-button]"));
  const tabPanels = Array.from(shell.ownerDocument.querySelectorAll("[data-finance-tab-panel]"));
  const researchTitleEl = shell.ownerDocument.querySelector("[data-finance-research-title]");
  const researchSubtitleEl = shell.ownerDocument.querySelector("[data-finance-research-subtitle]");
  const researchSourceEl = shell.ownerDocument.querySelector("[data-finance-research-source]");
  const researchHeroLineEl = shell.ownerDocument.querySelector("[data-finance-research-hero-line]");
  const researchSymbolEl = shell.ownerDocument.querySelector("[data-finance-research-symbol]");
  const researchCompanyEl = shell.ownerDocument.querySelector("[data-finance-research-company]");
  const researchExchangeEl = shell.ownerDocument.querySelector("[data-finance-research-exchange]");
  const researchPriceEl = shell.ownerDocument.querySelector("[data-finance-research-price]");
  const researchQuoteAsOfEl = shell.ownerDocument.querySelector("[data-finance-research-quote-as-of]");
  const researchSnapshotEl = shell.ownerDocument.querySelector("[data-finance-research-snapshot]");
  const researchShortInterestEl = shell.ownerDocument.querySelector("[data-finance-research-short-interest]");
  const researchShortVolumeEl = shell.ownerDocument.querySelector("[data-finance-research-short-volume]");
  const researchRelatedCompaniesEl = shell.ownerDocument.querySelector("[data-finance-research-related-companies]");
  const researchCompanySideEl = shell.ownerDocument.querySelector("[data-finance-research-company-side]");
  const researchSymbolSideEl = shell.ownerDocument.querySelector("[data-finance-research-symbol-side]");
  const researchExchangeSideEl = shell.ownerDocument.querySelector("[data-finance-research-exchange-side]");
  const researchFundamentalsEl = shell.ownerDocument.querySelector("[data-finance-research-fundamentals]");
  const researchFundamentalsShellEl = shell.ownerDocument.querySelector("[data-finance-research-fundamentals-shell]");
  const researchFundamentalsLoadingEl = shell.ownerDocument.querySelector("[data-finance-research-fundamentals-loading]");
  const researchNewsShellEl = shell.ownerDocument.querySelector("[data-finance-research-news-shell]");
  const researchNewsWebEl = shell.ownerDocument.querySelector("[data-finance-research-news-web]");
  const researchNewsWebMetaEl = shell.ownerDocument.querySelector("[data-finance-research-news-web-meta]");
  const researchNewsMassiveEl = shell.ownerDocument.querySelector("[data-finance-research-news-massive]");
  const researchNewsMassiveMetaEl = shell.ownerDocument.querySelector("[data-finance-research-news-massive-meta]");
  const researchNewsLoadingEl = shell.ownerDocument.querySelector("[data-finance-research-news-loading]");
  const researchFilingsShellEl = shell.ownerDocument.querySelector("[data-finance-research-filings-shell]");
  const researchFilingsSummaryEl = shell.ownerDocument.querySelector("[data-finance-research-filings-summary]");
  const researchFilingsListEl = shell.ownerDocument.querySelector("[data-finance-research-filings]");
  const researchFilingsLoadingEl = shell.ownerDocument.querySelector("[data-finance-research-filings-loading]");
  const researchSourceSummaryUrl = (shell.dataset.financeSourceSummaryUrl || "").trim();
  const researchSourcesEl = shell.ownerDocument.querySelector("[data-finance-research-sources]");
  const researchChartTitleEl = shell.ownerDocument.querySelector("[data-finance-research-chart-title]");
  const researchChartAsOfEl = shell.ownerDocument.querySelector("[data-finance-research-chart-as-of]");
  const researchChartPlaceholderEl = shell.ownerDocument.querySelector("[data-finance-research-chart-placeholder]");
  const researchChartLoadingEl = shell.ownerDocument.querySelector("[data-finance-research-chart-loading]");
  const researchChartTimeframeButtons = Array.from(shell.ownerDocument.querySelectorAll("[data-finance-research-chart-timeframe]"));
  const researchViewButtons = Array.from(shell.ownerDocument.querySelectorAll("[data-finance-research-view-button]"));
  const workspaceNameEl = shell.ownerDocument.querySelector("[data-finance-workspace-name]");
  const positionCountEl = shell.ownerDocument.querySelector("[data-finance-position-count]");
  const quoteCountEl = shell.ownerDocument.querySelector("[data-finance-quote-count]");
  const transactionCountEl = shell.ownerDocument.querySelector("[data-finance-transaction-count]");
  const quoteStatusEl = shell.ownerDocument.querySelector("[data-finance-quote-status]");
  const positionsGridEl = shell.ownerDocument.querySelector("[data-finance-positions-grid]");
  const tradeHistoryPanelEl = shell.ownerDocument.querySelector("[data-finance-trade-history-panel]");
  const tradeHistoryMetaEl = shell.ownerDocument.querySelector("[data-finance-trade-meta]");
  const tradeHistoryChartEl = shell.ownerDocument.querySelector("[data-finance-trade-chart]");
  const financeContextScript = document.getElementById("finance-system-context");
  const financeBootstrapScript = document.getElementById("finance-bootstrap");
  const stateUrl = (shell.dataset.financeStateUrl || "").trim();
  const parsedQuoteTtlSeconds = Number(shell.dataset.financeQuoteTtlSeconds || 120);
  const quoteTtlSeconds = Number.isFinite(parsedQuoteTtlSeconds) && parsedQuoteTtlSeconds > 0 ? parsedQuoteTtlSeconds : 120;
  const quoteWatchIntervalMs = quoteTtlSeconds * 1000;
  const autoFetchStorageKey = "finance:auto_fetch";

  let socket = null;
  let pendingPrompt = "";
  let connecting = false;
  let bootstrapSent = false;
  let autoFetchEnabled = loadAutoFetchPreference();
  let lastLocalPrompt = "";
  let snapshotPollTimer = null;
  let snapshotPollAttempts = 0;
  let snapshotWatchTimer = null;
  let snapshotWatchBusy = false;
  let lastSnapshotSignature = "";
  let positionsTableBodyEl = null;
  let positionsTableFootEl = null;
  let currentBootstrap = null;
  let currentResearchContext = null;
  let selectedPositionSymbol = "";
  let tradeMarkerTooltipEl = null;
  let activeTabName = "portfolio";
  let tickerSearchTimer = null;
  let tickerSearchRequestId = 0;
  let tickerResearchRequestId = 0;
  let lastTickerSearchMatches = [];
  let tickerResearchTimer = null;
  let tickerResearchAttempts = 0;
  let lastTickerResearchSymbol = "";
  let researchChartTimeframe = "daily";
  let activeResearchView = "dashboard";
  const researchSourceSummaryPending = new Set();

  function readJsonScript(script) {
    if (!script || !script.textContent) {
      return null;
    }
    try {
      return JSON.parse(script.textContent);
    } catch {
      return null;
    }
  }

  function loadAutoFetchPreference() {
    try {
      const raw = window.localStorage.getItem(autoFetchStorageKey);
      if (raw === null) {
        return defaultAutoFetch;
      }
      return raw === "true";
    } catch {
      return defaultAutoFetch;
    }
  }

  function saveAutoFetchPreference(enabled) {
    try {
      window.localStorage.setItem(autoFetchStorageKey, enabled ? "true" : "false");
    } catch {
      // Ignore storage failures.
    }
  }

  function getCsrfToken() {
    const cookieName = "csrftoken=";
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (const rawCookie of cookies) {
      const cookie = rawCookie.trim();
      if (cookie.startsWith(cookieName)) {
        return decodeURIComponent(cookie.slice(cookieName.length));
      }
    }
    return "";
  }

  function setStatus(text) {
    if (statusEl) {
      statusEl.textContent = text;
    }
  }

  function log(...args) {
    if (window.console && typeof window.console.debug === "function") {
      window.console.debug("[finance/chat]", ...args);
    }
  }

  function warn(...args) {
    if (window.console && typeof window.console.warn === "function") {
      window.console.warn("[finance/chat]", ...args);
    }
  }

  function scrollToBottom() {
    if (!messagesEl) {
      return;
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendMessage(kind, title, text) {
    if (!messagesEl) {
      return;
    }
    const article = document.createElement("article");
    article.className = `chat-message${kind === "system" ? " system" : ""}`;
    const label = document.createElement("strong");
    label.textContent = title;
    const body = document.createElement("p");
    body.textContent = text;
    article.append(label, body);
    messagesEl.appendChild(article);
    scrollToBottom();
  }

  function formatNumber(value, digits) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return "-";
    }
    return numeric.toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function formatPercent(value, digits = 2) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return "-";
    }
    return `${numeric.toFixed(digits)}%`;
  }

  const quoteBadgeTimeZone = "America/New_York";
  const quoteBadgeOpenMinute = 4 * 60;
  const quoteBadgeCloseMinute = 19 * 60;
  const quoteBadgePartsFormatter = new Intl.DateTimeFormat("en-US", {
    timeZone: quoteBadgeTimeZone,
    weekday: "short",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
    hourCycle: "h23",
    timeZoneName: "shortOffset",
  });

  function parseQuoteBadgeOffsetMinutes(label) {
    const text = String(label || "").trim();
    const match = text.match(/GMT([+-])(\d{1,2})(?::?(\d{2}))?/i);
    if (!match) {
      return 0;
    }
    const sign = match[1] === "-" ? -1 : 1;
    const hours = Number(match[2] || 0);
    const minutes = Number(match[3] || 0);
    return sign * (hours * 60 + minutes);
  }

  function getQuoteBadgeParts(date) {
    const parts = {};
    for (const part of quoteBadgePartsFormatter.formatToParts(date)) {
      if (part.type !== "literal") {
        parts[part.type] = part.value;
      }
    }
    return {
      year: Number(parts.year || 0),
      month: Number(parts.month || 0),
      day: Number(parts.day || 0),
      weekday: String(parts.weekday || ""),
      hour: Number(parts.hour || 0),
      minute: Number(parts.minute || 0),
      second: Number(parts.second || 0),
      offsetMinutes: parseQuoteBadgeOffsetMinutes(parts.timeZoneName),
    };
  }

  function buildQuoteBadgeEasternInstant(year, month, day, hour, minute, second) {
    const offsetProbe = new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
    const offsetMinutes = getQuoteBadgeParts(offsetProbe).offsetMinutes;
    return new Date(Date.UTC(year, month - 1, day, hour, minute, second) - (offsetMinutes * 60000));
  }

  function getQuoteBadgeNextMarketOpen(year, month, day) {
    let probe = new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
    for (let attempt = 0; attempt < 10; attempt += 1) {
      probe.setUTCDate(probe.getUTCDate() + 1);
      const parts = getQuoteBadgeParts(probe);
      if (parts.weekday === "Sat" || parts.weekday === "Sun") {
        continue;
      }
      return buildQuoteBadgeEasternInstant(parts.year, parts.month, parts.day, 4, 0, 0);
    }
    return probe;
  }

  function getQuoteBadgeMarketAwareAgeMinutes(value, now = new Date()) {
    const start = new Date(value);
    const end = new Date(now);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) {
      return 0;
    }
    let cursor = new Date(start);
    let totalMs = 0;
    for (let safety = 0; safety < 1000 && cursor < end; safety += 1) {
      const parts = getQuoteBadgeParts(cursor);
      if (parts.weekday === "Sat" || parts.weekday === "Sun") {
        cursor = getQuoteBadgeNextMarketOpen(parts.year, parts.month, parts.day);
        continue;
      }
      const open = buildQuoteBadgeEasternInstant(parts.year, parts.month, parts.day, 4, 0, 0);
      const close = buildQuoteBadgeEasternInstant(parts.year, parts.month, parts.day, 19, 0, 0);
      if (cursor < open) {
        cursor = open;
        if (cursor >= end) {
          break;
        }
      }
      if (cursor < close) {
        const segmentEnd = end < close ? end : close;
        totalMs += segmentEnd.getTime() - cursor.getTime();
        cursor = segmentEnd;
        if (cursor >= end) {
          break;
        }
      }
      cursor = getQuoteBadgeNextMarketOpen(parts.year, parts.month, parts.day);
    }
    return totalMs / 60000;
  }

  function getQuoteBadgeAgeClass(value) {
    const ageMinutes = getQuoteBadgeMarketAwareAgeMinutes(value);
    if (ageMinutes < 5) {
      return "age-fresh";
    }
    if (ageMinutes < 10) {
      return "age-warm";
    }
    if (ageMinutes < 30) {
      return "age-orange";
    }
    return "age-old";
  }

  function formatQuoteCacheBadge(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const hour = date.getHours() % 12 || 12;
    const minute = String(date.getMinutes()).padStart(2, "0");
    const suffix = date.getHours() >= 12 ? "p" : "a";
    return `${hour}:${minute}${suffix}`;
  }

  function formatQuoteCacheTooltip(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const ageMinutes = getQuoteBadgeMarketAwareAgeMinutes(value);
    const ageLabel = Number.isFinite(ageMinutes)
      ? `${Math.max(0, Math.round(ageMinutes))} minute${Math.round(ageMinutes) === 1 ? "" : "s"} market age`
      : "";
    return date.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }) + (ageLabel ? ` (${ageLabel})` : "");
  }

  function applyQuoteBadgeStyle(badge, value) {
    if (!badge) {
      return;
    }
    badge.classList.remove("age-fresh", "age-warm", "age-orange", "age-old");
    const ageClass = getQuoteBadgeAgeClass(value);
    if (ageClass) {
      badge.classList.add(ageClass);
    }
  }

  function formatResearchTimestamp(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "—";
    }
    return date.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
  }

  function formatHistoryRefreshBadge(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const monthDay = date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
    const hour = date.getHours() % 12 || 12;
    const minute = String(date.getMinutes()).padStart(2, "0");
    const suffix = date.getHours() >= 12 ? "p" : "a";
    return `${monthDay} ${hour}:${minute}${suffix}`;
  }

  function formatResearchPriceValue(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return "—";
    }
    const roundedToTwo = Math.round(numeric * 100) / 100;
    if (Math.abs(numeric - roundedToTwo) < 0.00005) {
      return roundedToTwo.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    }
    return numeric.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    });
  }

  function renderResearchPriceDisplay(price, asOf) {
    if (!researchPriceEl) {
      return;
    }
    researchPriceEl.innerHTML = "";
    const wrapper = document.createElement("span");
    wrapper.className = "research-price-stack";
    const value = document.createElement("span");
    value.className = "research-price-value";
    value.textContent = formatResearchPriceValue(price);
    wrapper.appendChild(value);
    const badgeText = formatQuoteCacheBadge(asOf);
    if (badgeText) {
      const badge = document.createElement("sup");
      badge.className = "research-price-badge";
      badge.textContent = badgeText;
      applyQuoteBadgeStyle(badge, asOf);
      const tooltip = formatQuoteCacheTooltip(asOf);
      if (tooltip) {
        badge.title = `Price cache updated ${tooltip}`;
      }
      wrapper.appendChild(badge);
    }
    researchPriceEl.appendChild(wrapper);
  }

  function extractResearchPrice(context) {
    const quoteCache = context && typeof context === "object" ? context.quote_cache || {} : {};
    const payload = quoteCache && typeof quoteCache === "object" ? quoteCache.payload || {} : {};
    const quote = payload.quote || {};
    const snapshot = payload.snapshot || {};
    const candidates = [
      quote.last,
      quote.last_price,
      quote.price,
      quote.close,
      payload.last_price,
      payload.last,
      payload.price,
      payload.close,
      snapshot?.min?.c,
      snapshot?.day?.c,
      snapshot?.prevDay?.c,
    ];
    for (const candidate of candidates) {
      const numeric = Number(candidate);
      if (Number.isFinite(numeric) && numeric > 0) {
        return numeric;
      }
    }
    return null;
  }

  function extractResearchHistoryCount(context) {
    const history = context && typeof context === "object" ? context.history_cache || {} : {};
    const payload = history && typeof history === "object" ? history.payload || {} : {};
    const bars = Array.isArray(payload.bars) ? payload.bars : Array.isArray(payload.candles) ? payload.candles : [];
    return bars.length;
  }

  function extractResearchQuoteSnapshot(context) {
    const quoteCache = context && typeof context === "object" ? context.quote_cache || {} : {};
    const payload = quoteCache && typeof quoteCache === "object" ? quoteCache.payload || {} : {};
    if (payload && typeof payload === "object" && payload.snapshot && typeof payload.snapshot === "object") {
      return payload.snapshot;
    }
    return payload && typeof payload === "object" ? payload : null;
  }

  function formatResearchValue(value, digits = 2) {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) {
      return numeric.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
    }
    const text = String(value ?? "").trim();
    return text || "—";
  }

  function formatResearchFundamentalValue(key, value) {
    const numeric = Number(value);
    const normalizedKey = String(key || "").toLowerCase();
    if (!Number.isFinite(numeric)) {
      const text = String(value ?? "").trim();
      if (!text) {
        return "—";
      }
      if (normalizedKey.includes("date")) {
        const date = new Date(text);
        if (!Number.isNaN(date.getTime())) {
          return date.toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
          });
        }
      }
      return text;
    }
    if (normalizedKey.includes("yield")) {
      return `${numeric.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
    }
    if (normalizedKey.includes("eps")) {
      return numeric.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    if (
      normalizedKey === "amount" ||
      normalizedKey.includes("dividend amount") ||
      (normalizedKey.includes("dividend") && normalizedKey.includes("amount")) ||
      normalizedKey.includes("div amount")
    ) {
      return numeric.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    if (normalizedKey.includes("volume") || normalizedKey.includes("shares") || normalizedKey.includes("marketcap") || normalizedKey.includes("market cap") || normalizedKey.includes("revenue") || normalizedKey.includes("income")) {
      return numeric.toLocaleString("en-US", { maximumFractionDigits: 0 });
    }
    if (normalizedKey.includes("ratio") || normalizedKey.includes("div")) {
      return numeric.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    }
    return numeric.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  function extractResearchFundamentalCache(context) {
    return context && typeof context === "object" && context.fundamental_cache && typeof context.fundamental_cache === "object"
      ? context.fundamental_cache
      : null;
  }

  function extractSchwabInstrumentFundamental(context) {
    const snapshot = extractResearchQuoteSnapshot(context) || {};
    const fundamentalCache = extractResearchFundamentalCache(context);
    const candidates = [
      snapshot.fundamentals && typeof snapshot.fundamentals === "object" ? snapshot.fundamentals.schwab_instrument : null,
      fundamentalCache ? fundamentalCache.payload : null,
    ];
    for (const candidate of candidates) {
      if (!candidate || typeof candidate !== "object") {
        continue;
      }
      if (candidate.fundamental && typeof candidate.fundamental === "object") {
        return candidate.fundamental;
      }
      if (candidate.snapshot && typeof candidate.snapshot === "object") {
        if (candidate.snapshot.fundamental && typeof candidate.snapshot.fundamental === "object") {
          return candidate.snapshot.fundamental;
        }
        return candidate.snapshot;
      }
      if (
        candidate.high52 !== undefined ||
        candidate.low52 !== undefined ||
        candidate.marketCap !== undefined ||
        candidate.symbol
      ) {
        return candidate;
      }
    }
    return {};
  }

  function buildResearchFundamentalSections(context) {
    const snapshot = extractResearchQuoteSnapshot(context) || {};
    const quote = snapshot.quote && typeof snapshot.quote === "object" ? snapshot.quote : {};
    const fundamental = snapshot.fundamental && typeof snapshot.fundamental === "object" ? snapshot.fundamental : {};
    const instrument = extractSchwabInstrumentFundamental(context);
    const latestPrice = extractResearchPrice(context);
    const sections = [];
    const row = (label, value, options = {}) => {
      const text = formatResearchFundamentalValue(label, value);
      if (!text || text === "—") {
        return null;
      }
      return {
        label,
        text: options.percent && !String(text).trim().endsWith("%") ? `${text}%` : text,
      };
    };

    const earnings = [
      row("P/E", instrument.peRatio ?? fundamental.peRatio),
      row("EPS", instrument.eps ?? fundamental.eps),
      row("EPS TTM", instrument.epsTTM),
      row("EPS Change TTM", instrument.epsChangePercentTTM, { percent: true }),
    ].filter(Boolean);
    if (earnings.length) {
      sections.push({ title: "Earnings", rows: earnings });
    }

    const dividendFreq = Number(instrument.dividendFreq);
    const dividendFrequencyLabel = dividendFreq === 12 ? "Monthly" : dividendFreq === 4 ? "Quarterly" : dividendFreq === 2 ? "Semi-annual" : dividendFreq === 1 ? "Annual" : "";
    const dividends = [
      row("Yield", instrument.dividendYield ?? fundamental.divYield),
      row("Amount", instrument.dividendAmount ?? fundamental.divAmount),
      row("Ex-div Date", instrument.dividendDate),
      row("3Y growth", instrument.divGrowthRate3Year),
      row("Frequency", dividendFrequencyLabel),
    ].filter(Boolean);
    if (dividends.length) {
      sections.push({ title: "Dividends", rows: dividends });
    }

    const growth = [
      row("PEG", instrument.pegRatio),
      row("Revenue change year", instrument.revChangeYear, { percent: true }),
      row("Revenue change TTM", instrument.revChangeTTM, { percent: true }),
    ].filter(Boolean);
    if (growth.length) {
      sections.push({ title: "Growth", rows: growth });
    }

    const bookValuePerShare = Number(instrument.bookValuePerShare);
    let computedPb = null;
    if (Number.isFinite(latestPrice) && latestPrice > 0 && Number.isFinite(bookValuePerShare) && bookValuePerShare > 0) {
      computedPb = latestPrice / bookValuePerShare;
    }
    const financial = [
      row("Quick ratio", instrument.quickRatio),
      row("Current ratio", instrument.currentRatio),
      row("Interest coverage", instrument.interestCoverage),
      row("LT debt/equity", instrument.ltDebtToEquity),
      row("Total debt/capital", instrument.totalDebtToCapital),
    ].filter(Boolean);
    if (financial.length) {
      sections.push({ title: "Financial", rows: financial });
    }

    if (!sections.length) {
      const source = fundamental && typeof fundamental === "object" ? fundamental : quote;
      const rows = [];
      for (const [key, value] of Object.entries(source)) {
        if (value === null || value === undefined || value === "") {
          continue;
        }
        if (typeof value === "object") {
          continue;
        }
        rows.push({
          label: key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/_/g, " "),
          text: formatResearchFundamentalValue(key, value),
        });
        if (rows.length >= 8) {
          break;
        }
      }
      if (rows.length) {
        sections.push({ title: "Fundamentals", rows });
      }
    }
    return sections;
  }

  function clearTickerSearchResults() {
    lastTickerSearchMatches = [];
    if (!searchResultsEl) {
      return;
    }
    searchResultsEl.innerHTML = "";
    searchResultsEl.hidden = true;
  }

  function renderTickerSearchResults(matches) {
    lastTickerSearchMatches = Array.isArray(matches) ? matches.slice(0, 10) : [];
    if (!searchResultsEl) {
      return;
    }
    searchResultsEl.innerHTML = "";
    if (!lastTickerSearchMatches.length) {
      searchResultsEl.hidden = true;
      return;
    }
    lastTickerSearchMatches.forEach((match, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ticker-search-result";
      button.dataset.symbol = String(match.symbol || "").trim().toUpperCase();
      button.dataset.index = String(index);

      const label = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = `${String(match.symbol || "").trim().toUpperCase()}${match.name ? ` · ${String(match.name)}` : ""}`;
      const subtitle = document.createElement("span");
      const parts = [match.exchange, match.asset_type].filter(Boolean);
      subtitle.textContent = parts.join(" · ") || "Cached universe match";
      label.append(title, subtitle);

      const meta = document.createElement("div");
      meta.className = "result-meta";
      meta.textContent = match.source_name ? String(match.source_name) : "";

      button.append(label, meta);
      const activateMatch = () => {
        void selectTickerFromSearch(match);
      };
      button.addEventListener("pointerdown", (event) => {
        if (event.button !== undefined && event.button !== 0) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        activateMatch();
      });
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        activateMatch();
      });
      button.addEventListener("dblclick", (event) => {
        event.preventDefault();
        event.stopPropagation();
        activateMatch();
      });
      button.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        activateMatch();
      });
      searchResultsEl.appendChild(button);
    });
    searchResultsEl.hidden = false;
  }

  function clearTickerResearchPoll() {
    if (tickerResearchTimer !== null) {
      window.clearTimeout(tickerResearchTimer);
      tickerResearchTimer = null;
    }
    tickerResearchAttempts = 0;
  }

  function isResearchContextReady(context) {
    if (!context || typeof context !== "object") {
      return false;
    }
    const quoteCache = context.quote_cache && typeof context.quote_cache === "object" ? context.quote_cache : null;
    const historyCache = context.history_cache && typeof context.history_cache === "object" ? context.history_cache : null;
    const fundamentalCache = context.fundamental_cache && typeof context.fundamental_cache === "object" ? context.fundamental_cache : null;
    const newsCache = context.news_cache && typeof context.news_cache === "object" ? context.news_cache : null;
    const newsMassiveCache = newsCache && typeof newsCache.massive === "object" ? newsCache.massive : null;
    const quoteReady = !!(quoteCache && String(quoteCache.as_of || "").trim());
    const historyPayload = historyCache && typeof historyCache.payload === "object" ? historyCache.payload : {};
    const barCount = Array.isArray(historyPayload.bars) ? historyPayload.bars.length : Array.isArray(historyPayload.candles) ? historyPayload.candles.length : 0;
    const fundamentalReady = !!(fundamentalCache && String(fundamentalCache.as_of || "").trim());
    const newsReady = !!(newsMassiveCache && String(newsMassiveCache.as_of || "").trim());
    const filingsReady = isFilingsReady(context);
    return quoteReady && barCount > 0 && fundamentalReady && newsReady && filingsReady;
  }

  function isFundamentalsReady(context) {
    if (!context || typeof context !== "object") {
      return false;
    }
    const fundamentalCache = context.fundamental_cache && typeof context.fundamental_cache === "object" ? context.fundamental_cache : null;
    return !!(fundamentalCache && String(fundamentalCache.as_of || "").trim());
  }

  function isNewsReady(context) {
    if (!context || typeof context !== "object") {
      return false;
    }
    const newsCache = context.news_cache && typeof context.news_cache === "object" ? context.news_cache : null;
    const webCache = newsCache && typeof newsCache.web_search === "object" ? newsCache.web_search : null;
    const massiveCache = newsCache && typeof newsCache.massive === "object" ? newsCache.massive : null;
    return !!(
      webCache && String(webCache.as_of || "").trim()
      && massiveCache && String(massiveCache.as_of || "").trim()
    );
  }

  function extractResearchFilingsCache(context) {
    if (!context || typeof context !== "object") {
      return null;
    }
    if (context.filings_cache && typeof context.filings_cache === "object") {
      return context.filings_cache;
    }
    const snapshot = context.research_snapshot && typeof context.research_snapshot === "object" ? context.research_snapshot : null;
    const payload = snapshot && typeof snapshot.payload === "object" ? snapshot.payload : null;
    if (payload && payload.filings_cache && typeof payload.filings_cache === "object") {
      return payload.filings_cache;
    }
    return null;
  }

  function isFilingsReady(context) {
    const filingsCache = extractResearchFilingsCache(context);
    return !!(filingsCache && String(filingsCache.as_of || "").trim());
  }

  function scheduleTickerResearchPoll(symbol) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!selectedSymbol) {
      return;
    }
    const maxAttempts = hasPendingResearchSourceSummaries() ? 30 : 6;
    log("ticker research poll schedule", {
      symbol: selectedSymbol,
      attempts: tickerResearchAttempts,
      maxAttempts,
      pendingSummaries: researchSourceSummaryPending.size,
    });
    if (tickerResearchTimer !== null) {
      window.clearTimeout(tickerResearchTimer);
      tickerResearchTimer = null;
    }
    if (tickerResearchAttempts >= maxAttempts) {
      warn("ticker research poll stopped", {
        symbol: selectedSymbol,
        attempts: tickerResearchAttempts,
        maxAttempts,
      });
      return;
    }
    tickerResearchTimer = window.setTimeout(() => {
      tickerResearchTimer = null;
      tickerResearchAttempts += 1;
      void loadTickerResearch(selectedSymbol, { queueRefresh: false, scheduleRetry: true, attempt: tickerResearchAttempts });
    }, 2000);
  }

  function setActiveFinanceTab(tabName) {
    activeTabName = tabName === "research" ? "research" : "portfolio";
    tabButtons.forEach((button) => {
      const isActive = String(button.dataset.financeTabButton || "") === activeTabName;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    tabPanels.forEach((panel) => {
      const isActive = String(panel.dataset.financeTabPanel || "") === activeTabName;
      panel.hidden = !isActive;
    });
  }

  function renderResearchContext(context) {
    if (!context || typeof context !== "object") {
      return;
    }
    currentResearchContext = context;
    const ticker = context.ticker && typeof context.ticker === "object" ? context.ticker : null;
    const quoteCache = context.quote_cache && typeof context.quote_cache === "object" ? context.quote_cache : null;
    const historyCache = context.history_cache && typeof context.history_cache === "object" ? context.history_cache : null;
    const fundamentalCache = context.fundamental_cache && typeof context.fundamental_cache === "object" ? context.fundamental_cache : null;
    const newsCache = context.news_cache && typeof context.news_cache === "object" ? context.news_cache : null;
    const newsWebCache = newsCache && typeof newsCache.web_search === "object" ? newsCache.web_search : null;
    const newsMassiveCache = newsCache && typeof newsCache.massive === "object" ? newsCache.massive : null;
    const filingsCache = extractResearchFilingsCache(context);
    const snapshot = context.research_snapshot && typeof context.research_snapshot === "object" ? context.research_snapshot : null;
    const symbol = String(context.symbol || ticker?.symbol || "").trim().toUpperCase();
    const company = String(ticker?.name || ticker?.company_name || ticker?.description || "Search for a ticker").trim();
    const exchange = String(ticker?.exchange || "").trim();
    const assetType = String(ticker?.asset_type || "").trim();
    const price = extractResearchPrice(context);
    const quoteAsOf = String(quoteCache?.as_of || "").trim();
    const snapshotSummary = String(snapshot?.summary_text || "").trim();
    const historyCount = extractResearchHistoryCount(context);
    const historyAsOf = String(historyCache?.as_of || "").trim();
    const newsWebReady = !!(newsWebCache && String(newsWebCache.as_of || "").trim());
    const newsMassiveReady = !!(newsMassiveCache && String(newsMassiveCache.as_of || "").trim());
    const filingsReady = !!(filingsCache && String(filingsCache.as_of || "").trim());
    const filingsSummaryKeys = filingsCache && typeof filingsCache.payload === "object" && filingsCache.payload.ai_summaries && typeof filingsCache.payload.ai_summaries === "object"
      ? Object.keys(filingsCache.payload.ai_summaries)
      : [];
    syncResearchSourceSummaryPending(filingsCache);
    log("renderResearchContext", {
      symbol,
      quoteAsOf,
      historyAsOf,
      filingsReady,
      filingsSummaryCount: filingsSummaryKeys.length,
      filingsSummaryKeys: filingsSummaryKeys.slice(0, 4),
      pendingSummaries: researchSourceSummaryPending.size,
    });

    if (researchTitleEl) {
      researchTitleEl.textContent = symbol ? `${symbol}: Research` : "Search a ticker to begin";
    }
    if (researchSubtitleEl) {
      researchSubtitleEl.textContent = symbol
        ? `Cached universe data is loaded first for ${symbol}. Research detail will expand as cached market data and future refreshes arrive.`
        : "Cached universe results load immediately. Research data will fill in from cached market data first, then queued data refreshes.";
    }
    if (researchSourceEl) {
      researchSourceEl.textContent = String(context.status || "Cached data ready").replace(/_/g, " ");
    }
    if (researchHeroLineEl) {
      const heroExchange = [exchange, assetType].filter(Boolean).join(" · ");
      const heroAsOf = quoteAsOf ? formatResearchTimestamp(quoteAsOf) : "";
      const heroText = [symbol, company, heroExchange].filter(Boolean).join(" • ");
      researchHeroLineEl.textContent = heroText ? `${heroText}${heroAsOf ? ` as of ${heroAsOf}` : ""}` : "Search a ticker to begin";
    }
    if (researchSymbolEl) {
      researchSymbolEl.textContent = symbol || "—";
    }
    if (researchCompanyEl) {
      researchCompanyEl.textContent = company || "Search for a ticker";
    }
    if (researchExchangeEl) {
      const exchangeParts = [exchange, assetType].filter(Boolean);
      researchExchangeEl.textContent = exchangeParts.length ? exchangeParts.join(" · ") : "—";
    }
    if (researchCompanySideEl) {
      researchCompanySideEl.textContent = company || "Search for a ticker";
    }
    if (researchSymbolSideEl) {
      researchSymbolSideEl.textContent = symbol || "—";
    }
    if (researchExchangeSideEl) {
      const exchangeParts = [exchange, assetType].filter(Boolean);
      researchExchangeSideEl.textContent = exchangeParts.length ? exchangeParts.join(" · ") : "—";
    }
    if (researchPriceEl) {
      renderResearchPriceDisplay(price, quoteAsOf);
    }
    if (researchQuoteAsOfEl) {
      researchQuoteAsOfEl.textContent = quoteAsOf ? formatResearchTimestamp(quoteAsOf) : "—";
    }
    if (researchSnapshotEl) {
      researchSnapshotEl.textContent = snapshotSummary || (historyCount ? `${historyCount} cached history bars` : "None cached yet");
    }
    if (researchShortInterestEl) {
      researchShortInterestEl.textContent = symbol ? "Awaiting short-interest data" : "Select a ticker";
    }
    if (researchShortVolumeEl) {
      researchShortVolumeEl.textContent = symbol ? "Awaiting short-volume data" : "Select a ticker";
    }
    if (researchRelatedCompaniesEl) {
      researchRelatedCompaniesEl.innerHTML = "";
      const relatedItem = document.createElement("div");
      relatedItem.className = "research-mini-item";
      const relatedTitle = document.createElement("strong");
      relatedTitle.textContent = symbol ? "Related companies coming next" : "Search a ticker";
      const relatedBody = document.createElement("span");
      relatedBody.textContent = symbol
        ? "The cached universe table will be used here to surface peers and close matches."
        : "Related companies will appear from the cached ticker universe once a symbol is selected.";
      relatedItem.append(relatedTitle, relatedBody);
      researchRelatedCompaniesEl.appendChild(relatedItem);
    }

    if (researchFundamentalsEl) {
      const fundamentalsReady = isFundamentalsReady(context);
      if (researchFundamentalsShellEl) {
        researchFundamentalsShellEl.classList.toggle("is-loading", symbol && !fundamentalsReady);
      }
      if (researchFundamentalsLoadingEl) {
        researchFundamentalsLoadingEl.setAttribute("aria-hidden", fundamentalsReady ? "true" : "false");
      }
      researchFundamentalsEl.innerHTML = "";
      const sections = buildResearchFundamentalSections(context);
      if (!sections.length) {
        sections.push({
          title: "Research cache",
          rows: [
            {
              label: "Status",
              text: symbol
                ? `Quote and fundamental fields will load here after the research refresh completes.${historyCount ? ` ${historyCount} cached price bars are available.` : ""}`
                : "Search a ticker to populate research fundamentals.",
            },
          ],
        });
      }
      if (!symbol || !fundamentalsReady) {
        const waitingSection = document.createElement("section");
        waitingSection.className = "research-fundamental-group";
        const waitingHeading = document.createElement("strong");
        waitingHeading.className = "research-fundamental-group-title";
        waitingHeading.textContent = "Loading";
        const waitingRow = document.createElement("div");
        waitingRow.className = "research-fundamental-row";
        const waitingLabel = document.createElement("strong");
        waitingLabel.textContent = symbol ? "Waiting for Schwab fundamentals" : "Search a ticker";
        const waitingBody = document.createElement("div");
        waitingBody.textContent = symbol
          ? "The research refresh is still fetching Schwab instrument fundamentals."
          : "Search for a symbol to hydrate the research pane from cached data.";
        waitingRow.append(waitingLabel, waitingBody);
        waitingSection.append(waitingHeading, waitingRow);
        researchFundamentalsEl.appendChild(waitingSection);
      }
      for (const section of sections) {
        const sectionEl = document.createElement("section");
        sectionEl.className = "research-fundamental-group";
        const heading = document.createElement("strong");
        heading.className = "research-fundamental-group-title";
        heading.textContent = section.title || "Fundamentals";
        sectionEl.appendChild(heading);
        for (const itemData of section.rows || []) {
          const item = document.createElement("div");
          item.className = "research-fundamental-row";
          const label = document.createElement("strong");
          label.textContent = itemData.label || "Field";
          const body = document.createElement("div");
          body.textContent = itemData.text || "—";
          item.append(label, body);
          sectionEl.appendChild(item);
        }
        researchFundamentalsEl.appendChild(sectionEl);
      }
    }

    if (researchNewsShellEl) {
      researchNewsShellEl.classList.toggle("is-loading", symbol && !newsMassiveReady);
    }
    if (researchNewsLoadingEl) {
      researchNewsLoadingEl.setAttribute("aria-hidden", newsMassiveReady ? "true" : "false");
    }
    if (researchNewsWebMetaEl) {
      const payload = newsWebCache && typeof newsWebCache.payload === "object" ? newsWebCache.payload : {};
      const newsCount = Array.isArray(payload.news) ? payload.news.length : Array.isArray(payload.results) ? payload.results.length : 0;
      const newsStatus = String(payload.status || "").trim().toLowerCase();
      researchNewsWebMetaEl.textContent = newsWebReady
        ? `${newsCount} item${newsCount === 1 ? "" : "s"} · ${formatResearchNewsTimestamp(String(newsWebCache?.as_of || "")) || formatResearchTimestamp(String(newsWebCache?.as_of || ""))}`
        : (newsStatus === "unavailable" ? "Unavailable" : "Waiting");
    }
    if (researchNewsMassiveMetaEl) {
      const payload = newsMassiveCache && typeof newsMassiveCache.payload === "object" ? newsMassiveCache.payload : {};
      const newsCount = Array.isArray(payload.news) ? payload.news.length : Array.isArray(payload.results) ? payload.results.length : 0;
      const newsStatus = String(payload.status || "").trim().toLowerCase();
      researchNewsMassiveMetaEl.textContent = newsMassiveReady
        ? `${newsCount} item${newsCount === 1 ? "" : "s"} · ${formatResearchNewsTimestamp(String(newsMassiveCache?.as_of || "")) || formatResearchTimestamp(String(newsMassiveCache?.as_of || ""))}`
        : (newsStatus === "unavailable" ? "Unavailable" : "Waiting");
    }
    if (researchNewsWebEl) {
      renderResearchNewsList(
        researchNewsWebEl,
        newsWebCache,
        {
          title: "Web search pending",
          body: "Search for a ticker to populate web search news.",
        },
        "Web",
      );
    }
    if (researchNewsMassiveEl) {
      renderResearchNewsList(
        researchNewsMassiveEl,
        newsMassiveCache,
        {
          title: "Massive news pending",
          body: "Search for a ticker to populate Massive news.",
        },
        "Massive",
      );
    }

    if (researchFilingsShellEl) {
      researchFilingsShellEl.classList.toggle("is-loading", symbol && !filingsReady);
    }
    if (researchFilingsLoadingEl) {
      researchFilingsLoadingEl.setAttribute("aria-hidden", filingsReady ? "true" : "false");
    }
    if (researchFilingsSummaryEl) {
      researchFilingsSummaryEl.innerHTML = "";
      const summaryItem = document.createElement("div");
      summaryItem.className = "research-filings-item";
      const summaryTitle = document.createElement("div");
      summaryTitle.className = "research-filings-item-title";
      const summaryLabel = document.createElement("strong");
      summaryLabel.textContent = filingsReady ? "SEC filings summary" : (symbol ? "Waiting for SEC filings" : "Search a ticker");
      const summaryState = document.createElement("span");
      summaryState.textContent = filingsReady
        ? "Critical filings are cached and summarized below."
        : (symbol ? "The queued research refresh is still fetching EDGAR filings." : "SEC filings will populate after a ticker is selected.");
      summaryTitle.append(summaryLabel, summaryState);
      summaryItem.appendChild(summaryTitle);
      const summaryBody = document.createElement("p");
      summaryBody.textContent = filingsReady
        ? buildResearchFilingSummary(filingsCache)
        : (symbol ? "The filings spinner stays active until the SEC cache entry arrives." : "Select a ticker to fetch the most recent 10-K, 10-Q, and 8-K filings.");
      summaryItem.appendChild(summaryBody);
      researchFilingsSummaryEl.appendChild(summaryItem);
    }
    if (researchFilingsListEl) {
      renderResearchFilingsList(
        researchFilingsListEl,
        filingsCache,
        {
          title: "SEC filings pending",
          body: "Search for a ticker to populate SEC filings.",
        },
      );
    }

    if (researchSourcesEl) {
      researchSourcesEl.innerHTML = "";
      const items = [];
      if (snapshotSummary) {
        items.push({ title: "Cached research snapshot", text: snapshotSummary });
      }
      if (quoteCache && quoteCache.cache_key) {
        items.push({ title: "Quote cache", text: `${quoteCache.cache_key}${quoteAsOf ? ` · ${formatResearchTimestamp(quoteAsOf)}` : ""}` });
      }
      if (historyCache && historyCache.cache_key) {
        items.push({ title: "Price history cache", text: `${historyCache.cache_key}${historyCache.timeframe ? ` · ${historyCache.timeframe}` : ""}` });
      }
      if (fundamentalCache && fundamentalCache.cache_key) {
        items.push({ title: "Fundamental cache", text: `${fundamentalCache.cache_key}${fundamentalCache.as_of ? ` · ${formatResearchTimestamp(fundamentalCache.as_of)}` : ""}` });
      }
      if (filingsCache && filingsCache.cache_key) {
        items.push({ title: "Filings cache", text: `${filingsCache.cache_key}${filingsCache.as_of ? ` · ${formatResearchTimestamp(filingsCache.as_of)}` : ""}` });
      }
      if (!items.length) {
        items.push({
          title: "Cached universe first",
          text: "Search results come from the local ticker universe table before any deeper research refresh.",
        });
      }
      for (const itemData of items) {
        const item = document.createElement("div");
        item.className = "research-source-item";
        const heading = document.createElement("strong");
        heading.textContent = itemData.title;
        const body = document.createElement("div");
        body.textContent = itemData.text;
        item.append(heading, body);
        researchSourcesEl.appendChild(item);
      }
    }
    if (researchChartTitleEl) {
      researchChartTitleEl.textContent = symbol ? `${symbol} daily OHLCV chart placeholder` : "Daily OHLCV chart placeholder";
    }
    if (researchChartAsOfEl) {
      researchChartAsOfEl.textContent = historyAsOf ? `Updated ${formatResearchTimestamp(historyAsOf)}` : "Awaiting selection";
    }
    if (researchChartPlaceholderEl) {
      researchChartPlaceholderEl.textContent = symbol
        ? `Select a ticker to load the daily OHLCV chart placeholder here. The current selection has ${historyCount ? `${historyCount} cached bars` : "no cached bars yet"}${historyAsOf ? `, last updated ${formatResearchTimestamp(historyAsOf)}` : ""}.`
        : "Select a ticker to load the daily OHLCV chart placeholder here. The research hydration step will populate this area first, before fundamentals, news, and filings grow around it.";
    }
    renderResearchChart(context);
  }

  function syncResearchChartControls() {
    for (const button of researchChartTimeframeButtons) {
      const timeframe = String(button.dataset.financeResearchChartTimeframe || "daily").trim();
      const isActive = timeframe === researchChartTimeframe;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    }
  }

  function syncResearchViewControls() {
    for (const button of researchViewButtons) {
      const viewName = String(button.dataset.financeResearchViewButton || "dashboard").trim() || "dashboard";
      const isActive = viewName === activeResearchView;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    }
  }

  function setResearchView(viewName) {
    activeResearchView = String(viewName || "dashboard").trim() || "dashboard";
    syncResearchViewControls();
  }

  function setResearchChartTimeframe(timeframe) {
    researchChartTimeframe = timeframe === "weekly" ? "weekly" : "daily";
    syncResearchChartControls();
    if (currentResearchContext) {
      renderResearchChart(currentResearchContext);
    }
  }

  function extractResearchNewsItems(cacheEntry) {
    if (!cacheEntry || typeof cacheEntry !== "object") {
      return [];
    }
    const payload = cacheEntry.payload && typeof cacheEntry.payload === "object" ? cacheEntry.payload : {};
    const items = Array.isArray(payload.news)
      ? payload.news
      : Array.isArray(payload.results)
        ? payload.results
        : [];
    return items
      .map((item) => {
        if (!item || typeof item !== "object") {
          return null;
        }
        return {
          title: extractNewsText(item.title || item.headline || item.name || ""),
          url: extractNewsUrl(item.article_url || item.url || item.link || item.href || ""),
          snippet: extractNewsText(item.description || item.snippet || item.summary || ""),
          publisher: extractNewsText(item.publisher || item.source || payload.provider || cacheEntry.source_name || ""),
          sourceUrl: extractNewsUrl(item.publisher_url || item.source_url || payload.source_url || payload.url || ""),
          publishedAt: String(item.published_utc || item.published || item.timestamp || "").trim(),
        };
      })
      .filter((item) => item && item.title);
  }

  function extractNewsText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    if (typeof value === "string") {
      return value.trim();
    }
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value).trim();
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        const text = extractNewsText(item);
        if (text) {
          return text;
        }
      }
      return "";
    }
    if (typeof value === "object") {
      for (const key of ["name", "title", "source", "publisher", "display_name", "displayName", "label", "text"]) {
        const text = extractNewsText(value[key]);
        if (text) {
          return text;
        }
      }
      const stringified = String(value).trim();
      return stringified && stringified !== "[object Object]" ? stringified : "";
    }
    return "";
  }

  function extractNewsUrl(value) {
    if (value === null || value === undefined) {
      return "";
    }
    if (typeof value === "string") {
      return value.trim();
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        const url = extractNewsUrl(item);
        if (url) {
          return url;
        }
      }
      return "";
    }
    if (typeof value === "object") {
      for (const key of ["url", "article_url", "href", "link", "source_url", "publisher_url"]) {
        const url = extractNewsUrl(value[key]);
        if (url) {
          return url;
        }
      }
    }
    return "";
  }

  function formatResearchNewsTimestamp(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const rounded = new Date(Math.round(date.getTime() / 60000) * 60000);
    const parts = new Intl.DateTimeFormat("en-US", {
      month: "numeric",
      day: "numeric",
      year: "2-digit",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZoneName: "short",
    }).formatToParts(rounded);
    const getPart = (type) => parts.find((part) => part.type === type)?.value || "";
    const month = getPart("month");
    const day = getPart("day");
    const year = getPart("year");
    const hour = getPart("hour");
    const minute = getPart("minute");
    const dayPeriod = getPart("dayPeriod");
    const timeZone = getPart("timeZoneName");
    const datePart = [month, day, year].filter(Boolean).join("/");
    const timePart = [hour, minute].filter(Boolean).join(":");
    return [datePart, timePart, dayPeriod, timeZone].filter(Boolean).join(" ");
  }

  function formatResearchShortDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const month = String(date.getMonth() + 1);
    const day = String(date.getDate());
    const year = String(date.getFullYear()).slice(-2);
    return `${month}/${day}/${year}`;
  }

  function renderResearchNewsList(container, cacheEntry, emptyMessage, labelPrefix) {
    if (!container) {
      return [];
    }
    container.innerHTML = "";
    const payload = cacheEntry && typeof cacheEntry === "object" && typeof cacheEntry.payload === "object"
      ? cacheEntry.payload
      : {};
    const status = String(payload.status || "").trim().toLowerCase();
    const items = extractResearchNewsItems(cacheEntry);
    if (!String(cacheEntry?.as_of || "").trim()) {
      const item = document.createElement("div");
      item.className = "research-news-item";
      const title = document.createElement("strong");
      title.textContent = emptyMessage.title;
      const body = document.createElement("div");
      body.textContent = emptyMessage.body;
      item.append(title, body);
      container.appendChild(item);
      return items;
    }
    if (status === "unavailable" || !items.length) {
      const item = document.createElement("div");
      item.className = "research-news-item";
      const reason = String(payload.message || emptyMessage.body || "No stories were returned.").trim();
      const title = document.createElement("strong");
      title.textContent = emptyMessage.title;
      const body = document.createElement("div");
      body.textContent = reason;
      item.append(title, body);
      container.appendChild(item);
      return items;
    }
    for (const itemData of items) {
      const item = document.createElement("div");
      item.className = "research-news-item";
      const title = document.createElement(itemData.url ? "a" : "strong");
      title.textContent = itemData.title;
      if (itemData.url) {
        title.href = itemData.url;
        title.target = "_blank";
        title.rel = "noopener noreferrer";
      }
      item.appendChild(title);
      const meta = document.createElement("div");
      meta.className = "research-news-item-meta";
      const sourceChip = document.createElement("span");
      sourceChip.textContent = labelPrefix;
      meta.appendChild(sourceChip);
      if (itemData.publisher) {
        const publisherChip = document.createElement("span");
        publisherChip.textContent = itemData.publisher;
        meta.appendChild(publisherChip);
      }
      if (itemData.publishedAt) {
        const timeChip = document.createElement("span");
        timeChip.textContent = formatResearchNewsTimestamp(itemData.publishedAt) || itemData.publishedAt;
        meta.appendChild(timeChip);
      }
      item.appendChild(meta);
      if (itemData.snippet) {
        const body = document.createElement("p");
        body.textContent = itemData.snippet;
        item.appendChild(body);
      }
      const footer = document.createElement("div");
      footer.className = "research-news-item-footer";
      const footerSource = document.createElement("span");
      footerSource.textContent = `Source: ${itemData.publisher || labelPrefix}`;
      footer.appendChild(footerSource);
      const footerUrl = itemData.sourceUrl || itemData.url;
      if (footerUrl) {
        const footerLink = document.createElement("a");
        footerLink.href = footerUrl;
        footerLink.textContent = footerUrl;
        footerLink.target = "_blank";
        footerLink.rel = "noopener noreferrer";
        footer.appendChild(footerLink);
      }
      item.appendChild(footer);
      container.appendChild(item);
    }
    return items;
  }

  function extractResearchFilingItems(cacheEntry) {
    if (!cacheEntry || typeof cacheEntry !== "object") {
      return [];
    }
    const payload = cacheEntry.payload && typeof cacheEntry.payload === "object" ? cacheEntry.payload : {};
    const summaryMap = payload.ai_summaries && typeof payload.ai_summaries === "object" ? payload.ai_summaries : {};
    const cik = String(payload.cik || payload.cik_str || "").trim();
    const items = Array.isArray(payload.filings)
      ? payload.filings
      : Array.isArray(payload.results)
        ? payload.results
        : [];
    return items
      .map((item) => {
        if (!item || typeof item !== "object") {
          return null;
        }
        return {
          form: String(item.form || item.type || "").trim().toUpperCase(),
          filingDate: String(item.filing_date || item.filingDate || "").trim(),
          reportDate: String(item.report_date || item.reportDate || "").trim(),
          accessionNumber: String(item.accession_number || item.accessionNumber || "").trim(),
          primaryDocument: String(item.primary_document || item.primaryDocument || "").trim(),
          description: String(item.description || item.summary || "").trim(),
          url: buildResearchFilingUrl(item, cik),
          aiSummary: buildResearchFilingUrl(item, cik)
            ? summaryMap[buildResearchFilingUrl(item, cik)] || null
            : null,
        };
      })
      .filter((item) => item && item.form);
  }

  function buildResearchFilingUrl(item, cik) {
    const filingUrl = String(item?.filing_url || item?.url || item?.link || "").trim();
    if (filingUrl) {
      return filingUrl;
    }
    const accessionNumber = String(item?.accession_number || item?.accessionNumber || "").trim();
    const primaryDocument = String(item?.primary_document || item?.primaryDocument || "").trim();
    const normalizedCik = String(cik || "").trim();
    if (!normalizedCik || !accessionNumber || !primaryDocument) {
      return "";
    }
    const accessionClean = accessionNumber.replace(/-/g, "");
    return accessionClean ? `https://www.sec.gov/Archives/edgar/data/${normalizedCik}/${accessionClean}/${primaryDocument}` : "";
  }

  function getResearchSourceSummaryState(cacheEntry, sourceUrl) {
    const payload = cacheEntry && typeof cacheEntry === "object" && typeof cacheEntry.payload === "object"
      ? cacheEntry.payload
      : {};
    const summaryMap = payload.ai_summaries && typeof payload.ai_summaries === "object" ? payload.ai_summaries : {};
    const key = String(sourceUrl || "").trim();
    if (key && researchSourceSummaryPending.has(key)) {
      log("resolve source summary state", {
        sourceUrl: key,
        hasSummary: !!summaryMap[key],
        summaryKeys: Object.keys(summaryMap).slice(0, 4),
      });
    }
    return key ? summaryMap[key] || null : null;
  }

  function syncResearchSourceSummaryPending(cacheEntry) {
    const payload = cacheEntry && typeof cacheEntry === "object" && typeof cacheEntry.payload === "object"
      ? cacheEntry.payload
      : {};
    const summaryMap = payload.ai_summaries && typeof payload.ai_summaries === "object" ? payload.ai_summaries : {};
    if (!researchSourceSummaryPending.size || !Object.keys(summaryMap).length) {
      return;
    }
    const cleared = [];
    for (const [sourceUrl, state] of Object.entries(summaryMap)) {
      if (!researchSourceSummaryPending.has(sourceUrl) || !state || typeof state !== "object") {
        continue;
      }
      const status = String(state.status || "").trim().toLowerCase();
      const summaryText = String(state.summary_text || "").trim();
      const summaryLines = Array.isArray(state.summary_lines) ? state.summary_lines : [];
      const terminalState = status === "ready"
        || status === "error"
        || ((summaryText || summaryLines.length) && status !== "queued" && status !== "running");
      if (!terminalState) {
        continue;
      }
      researchSourceSummaryPending.delete(sourceUrl);
      cleared.push({
        sourceUrl,
        status: status || "unknown",
      });
    }
    if (cleared.length) {
      log("sync source summary pending", {
        cleared,
        pendingSummaries: researchSourceSummaryPending.size,
        summaryKeys: Object.keys(summaryMap).slice(0, 4),
      });
    }
  }

  function setResearchSourceSummaryPending(sourceUrl, pending) {
    const key = String(sourceUrl || "").trim();
    if (!key) {
      return;
    }
    if (pending) {
      researchSourceSummaryPending.add(key);
      return;
    }
    researchSourceSummaryPending.delete(key);
  }

  function isResearchSourceSummaryPending(sourceUrl) {
    const key = String(sourceUrl || "").trim();
    return key ? researchSourceSummaryPending.has(key) : false;
  }

  function hasPendingResearchSourceSummaries() {
    return researchSourceSummaryPending.size > 0;
  }

  function createResearchAiSummaryIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.classList.add("research-ai-summary-icon");

    const shield = document.createElementNS("http://www.w3.org/2000/svg", "path");
    shield.setAttribute("d", "M12 2.5 19.5 6v5.6c0 4.7-3 8.8-7.5 10.9C7.5 20.4 4.5 16.3 4.5 11.6V6L12 2.5Z");
    shield.setAttribute("fill", "#1d4ed8");
    shield.setAttribute("opacity", "0.95");

    const face = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    face.setAttribute("x", "6.3");
    face.setAttribute("y", "6.7");
    face.setAttribute("width", "11.4");
    face.setAttribute("height", "8.2");
    face.setAttribute("rx", "3.6");
    face.setAttribute("fill", "#0f172a");
    face.setAttribute("stroke", "#dbeafe");
    face.setAttribute("stroke-width", "0.8");

    const leftEye = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    leftEye.setAttribute("cx", "9.2");
    leftEye.setAttribute("cy", "10.6");
    leftEye.setAttribute("r", "1.2");
    leftEye.setAttribute("fill", "#f8fafc");

    const rightEye = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    rightEye.setAttribute("cx", "14.8");
    rightEye.setAttribute("cy", "10.6");
    rightEye.setAttribute("r", "1.2");
    rightEye.setAttribute("fill", "#f8fafc");

    const mouth = document.createElementNS("http://www.w3.org/2000/svg", "path");
    mouth.setAttribute("d", "M10.3 13.3c.6.6 1.3.9 1.7.9.4 0 1.1-.3 1.7-.9");
    mouth.setAttribute("fill", "none");
    mouth.setAttribute("stroke", "#dbeafe");
    mouth.setAttribute("stroke-width", "1.1");
    mouth.setAttribute("stroke-linecap", "round");

    const sparkleOne = document.createElementNS("http://www.w3.org/2000/svg", "path");
    sparkleOne.setAttribute("d", "M18.1 6.1 18.6 7.4 19.9 7.9 18.6 8.4 18.1 9.7 17.6 8.4 16.3 7.9 17.6 7.4Z");
    sparkleOne.setAttribute("fill", "#f8fafc");

    const sparkleTwo = document.createElementNS("http://www.w3.org/2000/svg", "path");
    sparkleTwo.setAttribute("d", "M14.6 4.8 14.9 5.5 15.6 5.8 14.9 6.1 14.6 6.8 14.3 6.1 13.6 5.8 14.3 5.5Z");
    sparkleTwo.setAttribute("fill", "#e0f2fe");

    svg.append(shield, face, leftEye, rightEye, mouth, sparkleOne, sparkleTwo);
    return svg;
  }

  function createResearchAiSummarySpinner() {
    const spinner = document.createElement("span");
    spinner.className = "research-ai-summary-spinner";
    spinner.setAttribute("aria-hidden", "true");
    return spinner;
  }

  function createResearchAiSummaryButton(itemData, cacheEntry, summaryState) {
    if (!itemData || !itemData.url || !researchSourceSummaryUrl) {
      return null;
    }
    const isLoading = !!summaryState?.isBusy;
    const isReady = !!summaryState?.isReady;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "research-ai-summary-button";
    button.dataset.financeAiSummaryButton = "true";
    button.dataset.financeAiSummaryParentCacheKey = String(cacheEntry?.cache_key || "");
    button.dataset.financeAiSummarySourceUrl = String(itemData.url || "").trim();
    button.dataset.financeAiSummarySourceTitle = String(itemData.form || itemData.url || "").trim();
    button.dataset.financeAiSummarySourceKind = "sec_filing";
    button.title = isReady ? "Summary already exists" : "Summarize with AI";
    button.setAttribute("aria-label", isReady ? "Summary already exists" : "Summarize with AI");
    button.classList.toggle("is-loading", isLoading);
    button.classList.toggle("is-ready", isReady);
    button.disabled = isLoading || isReady;
    if (isReady) {
      button.setAttribute("aria-disabled", "true");
    } else {
      button.removeAttribute("aria-disabled");
    }
    button.append(createResearchAiSummaryIcon(), createResearchAiSummarySpinner());
    return button;
  }

  function normalizeResearchSummaryLines(state, itemData) {
    const summaryText = String(state?.summary_text || "").trim();
    const summaryLines = Array.isArray(state?.summary_lines) ? state.summary_lines : [];
    const status = String(state?.status || "").trim().toLowerCase();
    if (status === "ready") {
      if (summaryLines.length) {
        return summaryLines.slice(0, 6).map((line) => String(line || "").trim()).filter(Boolean);
      }
      if (summaryText) {
        return summaryText
          .replace(/\r/g, "\n")
          .split(/\n+/)
          .map((line) => line.trim())
          .filter(Boolean)
          .slice(0, 6);
      }
    }
    if (status === "error") {
      return summaryLines.length
        ? summaryLines.slice(0, 6).map((line) => String(line || "").trim()).filter(Boolean)
        : [
            `AI summary failed for ${itemData?.form || "this filing"}.`,
            "Use the AI button to retry the fetch.",
            "If the page is blocked or paywalled, the card will show that condition.",
            "The card will update again once a successful headless run returns.",
            "The finance agent can retry the same source once the issue clears.",
            "This card stays ready for another background summary request.",
          ];
    }
    if (status === "queued" || status === "running") {
      return summaryLines.length
        ? summaryLines.slice(0, 6).map((line) => String(line || "").trim()).filter(Boolean)
        : [
            `Fetching ${itemData?.form || "this filing"} for AI summary.`,
            "The finance agent is loading the linked source now.",
            "This placeholder stays in place while the headless run works.",
            "The loading circle remains visible until the summary returns.",
            "The same button can be used again if the source needs a refresh.",
            "The summary will replace these lines when the cache updates.",
          ];
    }
    return [
      `Click the AI button to summarize ${itemData?.form || "this filing"}.`,
      "The headless run will fetch the linked URL and return a short note.",
      "The summary area is intentionally capped to a compact 5-6 line block.",
      "This component is reusable for any linked research card.",
      "The icon-only button keeps the card visually quiet.",
      "The spinner shows while the AI is working in the background.",
    ];
  }

  function renderResearchSummaryLines(container, lines) {
    if (!container) {
      return;
    }
    container.innerHTML = "";
    const normalized = Array.isArray(lines) ? lines.slice(0, 6) : [];
    const visibleLines = normalized.length ? normalized : ["Summary will appear here once the AI run completes."];
    for (const text of visibleLines) {
      const line = document.createElement("div");
      line.className = "research-filings-summary-line";
      line.textContent = String(text || "").trim();
      container.appendChild(line);
    }
  }

  function buildResearchFilingSummary(cacheEntry) {
    const payload = cacheEntry && typeof cacheEntry === "object" && typeof cacheEntry.payload === "object"
      ? cacheEntry.payload
      : {};
    const items = extractResearchFilingItems(cacheEntry);
    if (!String(cacheEntry?.as_of || "").trim()) {
      return payload.message || "Search a ticker to queue the SEC filings refresh.";
    }
    if (!items.length) {
      return payload.message || "No critical SEC filings were returned for this ticker.";
    }
    const latestByForm = new Map();
    for (const item of items) {
      if (!latestByForm.has(item.form)) {
        latestByForm.set(item.form, item);
      }
    }
    const parts = [];
    for (const form of ["10-K", "10-Q", "8-K"]) {
      const item = latestByForm.get(form);
      if (!item) {
        continue;
      }
      const partsForItem = [form];
      if (item.filingDate) {
        partsForItem.push(item.filingDate);
      }
      parts.push(partsForItem.join(" "));
    }
    if (!parts.length) {
      return `${items.length} SEC filing${items.length === 1 ? "" : "s"} cached.`;
    }
    return `Critical filings cached: ${parts.join(" · ")}.`;
  }

  function renderResearchFilingsList(container, cacheEntry, emptyMessage) {
    if (!container) {
      return [];
    }
    container.innerHTML = "";
    const payload = cacheEntry && typeof cacheEntry === "object" && typeof cacheEntry.payload === "object"
      ? cacheEntry.payload
      : {};
    const status = String(payload.status || "").trim().toLowerCase();
    const items = extractResearchFilingItems(cacheEntry);
    if (!String(cacheEntry?.as_of || "").trim()) {
      const item = document.createElement("div");
      item.className = "research-filings-item";
      const header = document.createElement("div");
      header.className = "research-filings-item-title";
      const title = document.createElement("strong");
      title.textContent = emptyMessage.title;
      header.appendChild(title);
      item.appendChild(header);
      const body = document.createElement("p");
      body.textContent = emptyMessage.body;
      item.appendChild(body);
      const summary = document.createElement("div");
      summary.className = "research-filings-summary-placeholder";
      renderResearchSummaryLines(summary, [
        "Click the AI button to summarize a filing once a URL is available.",
        "The headless run will fetch the linked page and return a short note.",
        "The card stays compact so the chat rail remains readable.",
        "This placeholder will be replaced by the returned summary text.",
        "The summary area will hold five to six short lines.",
        "The button stays icon-only so it fits the card header cleanly.",
      ]);
      item.appendChild(summary);
      container.appendChild(item);
      return items;
    }
    if (status === "unavailable" || status === "error" || !items.length) {
      const item = document.createElement("div");
      item.className = "research-filings-item";
      const header = document.createElement("div");
      header.className = "research-filings-item-title";
      const title = document.createElement("strong");
      title.textContent = emptyMessage.title;
      header.appendChild(title);
      item.appendChild(header);
      const body = document.createElement("p");
      body.textContent = String(payload.message || emptyMessage.body || "No filings were returned.").trim();
      item.appendChild(body);
      const summary = document.createElement("div");
      summary.className = "research-filings-summary-placeholder";
      renderResearchSummaryLines(summary, [
        "Search a ticker to queue the SEC filings refresh again.",
        "The summary area stays ready for a linked-source AI note.",
        "This card uses the same reusable pattern as any linked source.",
        "A successful run will replace this placeholder with the summary.",
        "The loading state will appear in the top-right button.",
        "The card remains narrow enough for the chat-side rail.",
      ]);
      item.appendChild(summary);
      container.appendChild(item);
      return items;
    }
    for (const itemData of items) {
      const item = document.createElement("div");
      item.className = "research-filings-item";
      const aiSummaryState = getResearchSourceSummaryState(cacheEntry, itemData.url);
      const aiSummaryStatus = String(aiSummaryState?.status || "").trim().toLowerCase();
      const isAiSummaryBusy = isResearchSourceSummaryPending(itemData.url) || aiSummaryStatus === "queued" || aiSummaryStatus === "running";
      const aiSummaryRenderState = aiSummaryState || (isAiSummaryBusy ? { status: "queued" } : null);
      item.classList.toggle("is-loading", isAiSummaryBusy);
      if (aiSummaryStatus === "ready" || aiSummaryStatus === "error") {
        setResearchSourceSummaryPending(itemData.url, false);
      }
      const header = document.createElement("div");
      header.className = "research-filings-item-title";
      const title = document.createElement("strong");
      const filedDate = formatResearchShortDate(itemData.filingDate);
      title.textContent = filedDate ? `${itemData.form} filed ${filedDate}` : itemData.form;
      header.appendChild(title);
      item.appendChild(header);
      if (itemData.url) {
        const link = document.createElement("a");
        link.href = itemData.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = itemData.url;
        item.appendChild(link);
      }
      const aiButton = createResearchAiSummaryButton(itemData, cacheEntry, {
        isBusy: isAiSummaryBusy,
        isReady: aiSummaryStatus === "ready",
      });
      if (aiButton) {
        item.appendChild(aiButton);
      }
      if (isAiSummaryBusy) {
        const itemLoading = document.createElement("div");
        itemLoading.className = "research-filings-item-loading";
        itemLoading.setAttribute("aria-hidden", "true");
        item.appendChild(itemLoading);
      }
      const summary = document.createElement("div");
      summary.className = "research-filings-summary-placeholder";
      renderResearchSummaryLines(summary, normalizeResearchSummaryLines(aiSummaryRenderState, itemData));
      item.appendChild(summary);
      container.appendChild(item);
    }
    return items;
  }

  async function requestResearchSourceSummary(button) {
    if (!button || !researchSourceSummaryUrl) {
      return false;
    }
    if (button.disabled) {
      return false;
    }
    const parentCacheKey = String(button.dataset.financeAiSummaryParentCacheKey || "").trim();
    const sourceUrl = String(button.dataset.financeAiSummarySourceUrl || "").trim();
    const sourceTitle = String(button.dataset.financeAiSummarySourceTitle || "").trim();
    const sourceKind = String(button.dataset.financeAiSummarySourceKind || "source").trim() || "source";
    if (!parentCacheKey || !sourceUrl) {
      return false;
    }
    setResearchSourceSummaryPending(sourceUrl, true);
    if (currentResearchContext) {
      renderResearchContext(currentResearchContext);
    }
    try {
      const response = await fetch(researchSourceSummaryUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify({
          parent_cache_key: parentCacheKey,
          source_url: sourceUrl,
          source_title: sourceTitle,
          source_kind: sourceKind,
          summary_lines: 6,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        setResearchSourceSummaryPending(sourceUrl, false);
        if (currentResearchContext) {
          renderResearchContext(currentResearchContext);
        }
        warn("source summary request failed", response.status, payload);
        return false;
      }
      tickerResearchAttempts = 0;
      const symbol = String(lastTickerResearchSymbol || currentResearchContext?.symbol || "").trim().toUpperCase();
      if (symbol) {
        scheduleTickerResearchPoll(symbol);
      }
      return true;
    } catch (error) {
      setResearchSourceSummaryPending(sourceUrl, false);
      if (currentResearchContext) {
        renderResearchContext(currentResearchContext);
      }
      warn("source summary request error", error);
      return false;
    }
  }

  function parseResearchHistoryCandles(context) {
    const historyCache = context && typeof context === "object" ? context.history_cache || {} : {};
    const payload = historyCache && typeof historyCache === "object" ? historyCache.payload || {} : {};
    const bars = Array.isArray(payload.bars)
      ? payload.bars
      : Array.isArray(payload.candles)
        ? payload.candles
        : Array.isArray(payload.results)
          ? payload.results
          : [];
    const normalized = bars
      .map((bar) => {
        if (!bar || typeof bar !== "object") {
          return null;
        }
        const timestampValue = bar.timestamp ?? bar.time ?? bar.datetime ?? bar.date ?? bar.t ?? bar.startDate ?? bar.start ?? 0;
        const timestamp = Number(timestampValue) || new Date(timestampValue).getTime();
        const open = Number(bar.open ?? bar.o);
        const high = Number(bar.high ?? bar.h);
        const low = Number(bar.low ?? bar.l);
        const close = Number(bar.close ?? bar.c);
        const volume = Number(bar.volume ?? bar.v ?? 0);
        if (!Number.isFinite(timestamp) || !Number.isFinite(open) || !Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close)) {
          return null;
        }
        return {
          timestamp,
          open,
          high,
          low,
          close,
          volume: Number.isFinite(volume) ? volume : 0,
        };
      })
      .filter(Boolean)
      .sort((left, right) => left.timestamp - right.timestamp);
    return dedupeCandlesByUtcDate(normalized);
  }

  function getResearchWeekStart(timestamp) {
    const date = new Date(timestamp);
    const day = date.getDay();
    const delta = day === 0 ? 6 : day - 1;
    date.setHours(0, 0, 0, 0);
    date.setDate(date.getDate() - delta);
    return date.getTime();
  }

  function aggregateResearchWeeklyCandles(dailyCandles) {
    const buckets = new Map();
    for (const candle of dailyCandles) {
      const weekStart = getResearchWeekStart(candle.timestamp);
      const bucket = buckets.get(weekStart) || {
        timestamp: weekStart,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
      };
      bucket.high = Math.max(bucket.high, candle.high);
      bucket.low = Math.min(bucket.low, candle.low);
      bucket.close = candle.close;
      bucket.volume += candle.volume;
      buckets.set(weekStart, bucket);
    }
    return Array.from(buckets.values()).sort((left, right) => left.timestamp - right.timestamp);
  }

  function getResearchChartCandles(context) {
    const dailyCandles = parseResearchHistoryCandles(context);
    if (!dailyCandles.length) {
      return [];
    }
    const quotePrice = extractResearchPrice(context);
    const mergedDailyCandles = mergeLatestQuoteIntoCandles(dailyCandles, quotePrice);
    if (researchChartTimeframe === "weekly") {
      return aggregateResearchWeeklyCandles(mergedDailyCandles);
    }
    return mergedDailyCandles;
  }

  function mergeLatestQuoteIntoCandles(candles, quotePrice) {
    const numericQuote = Number(quotePrice);
    if (!Array.isArray(candles) || !candles.length) {
      return Array.isArray(candles) ? candles.slice() : [];
    }
    if (!Number.isFinite(numericQuote) || numericQuote <= 0) {
      return candles.map((candle) => ({ ...candle }));
    }
    const merged = candles.map((candle) => ({ ...candle }));
    const last = merged[merged.length - 1];
    if (!last || typeof last !== "object") {
      return merged;
    }
    last.close = numericQuote;
    if (!Number.isFinite(last.high) || numericQuote > last.high) {
      last.high = numericQuote;
    }
    if (!Number.isFinite(last.low) || numericQuote < last.low) {
      last.low = numericQuote;
    }
    if (!Number.isFinite(last.open) || last.open <= 0) {
      last.open = numericQuote;
    }
    return merged;
  }

  function formatResearchChartTooltip(candle) {
    if (!candle) {
      return "";
    }
    const lines = [
      formatTradeDate(new Date(candle.timestamp)),
      `O: ${formatNumber(candle.open, 2)}`,
      `H: ${formatNumber(candle.high, 2)}`,
      `L: ${formatNumber(candle.low, 2)}`,
      `C: ${formatNumber(candle.close, 2)}`,
    ];
    if (Number.isFinite(candle.volume) && candle.volume > 0) {
      lines.push(`V: ${formatNumber(candle.volume, 0)}`);
    }
    return lines.join("\n");
  }

  function renderResearchChart(context) {
    if (!researchChartPlaceholderEl || !researchChartTitleEl || !researchChartAsOfEl) {
      return;
    }
    syncResearchChartControls();
    const ticker = context && typeof context === "object" ? context.ticker || {} : {};
    const symbol = String(context?.symbol || ticker?.symbol || "").trim().toUpperCase();
    const historyCandles = getResearchChartCandles(context);
    const historyCache = context && typeof context === "object" ? context.history_cache || {} : {};
    const historyAsOf = String(historyCache?.as_of || "").trim();
    const chartLabel = researchChartTimeframe === "weekly" ? "Weekly" : "Daily";
    researchChartTitleEl.textContent = symbol ? `${symbol}: ${chartLabel} OHLCV` : `${chartLabel} OHLCV`;
    researchChartAsOfEl.textContent = historyAsOf ? `Updated ${formatResearchTimestamp(historyAsOf)}` : "Awaiting selection";
    if (researchChartLoadingEl && researchChartLoadingEl.parentElement) {
      researchChartLoadingEl.parentElement.classList.toggle("is-loading", !!symbol && !historyCandles.length);
    }
    if (!symbol || !historyCandles.length) {
      researchChartPlaceholderEl.textContent = symbol
        ? `Select a ticker to load the ${chartLabel.toLowerCase()} OHLCV chart here. The chart will populate once the queued history refresh finishes.`
        : `Select a ticker to load the ${chartLabel.toLowerCase()} OHLCV chart here. The research hydration step will populate this area first, before fundamentals, news, and filings grow around it.`;
      if (researchChartLoadingEl) {
        researchChartLoadingEl.setAttribute("aria-hidden", symbol ? "true" : "true");
      }
      return;
    }
    if (researchChartLoadingEl) {
      researchChartLoadingEl.setAttribute("aria-hidden", "true");
    }

    const width = Math.max(320, researchChartPlaceholderEl.clientWidth || 520);
    const height = 320;
    const margin = { top: 18, right: 18, bottom: 58, left: 56 };
    const innerWidth = Math.max(1, width - margin.left - margin.right);
    const innerHeight = Math.max(1, height - margin.top - margin.bottom);
    const count = historyCandles.length;
    const minTime = historyCandles[0].timestamp;
    const maxTime = historyCandles[historyCandles.length - 1].timestamp;
    const xScale = (time) => {
      if (maxTime === minTime) {
        return margin.left + innerWidth / 2;
      }
      return margin.left + (((time - minTime) / (maxTime - minTime)) * innerWidth);
    };
    const bodyWidth = Math.max(5, Math.min(18, (count > 1 ? innerWidth / (count - 1) : innerWidth) * 0.54));
    const prices = [];
    for (const candle of historyCandles) {
      prices.push(candle.high, candle.low);
    }
    let minPrice = Math.min(...prices);
    let maxPrice = Math.max(...prices);
    if (minPrice === maxPrice) {
      const padding = minPrice === 0 ? 1 : Math.abs(minPrice) * 0.1;
      minPrice -= padding;
      maxPrice += padding;
    } else {
      const padding = (maxPrice - minPrice) * 0.1;
      minPrice -= padding;
      maxPrice += padding;
    }
    const yScale = (price) => {
      if (maxPrice === minPrice) {
        return margin.top + innerHeight / 2;
      }
      return margin.top + innerHeight - (((price - minPrice) / (maxPrice - minPrice)) * innerHeight);
    };
    const priceAxis = buildPriceAxisTicks(minPrice, maxPrice);
    minPrice = priceAxis.axisMin;
    maxPrice = priceAxis.axisMax;
    const priceTicks = priceAxis.ticks;
    const monthTickDates = getMonthBoundaryTicks(minTime, maxTime).map((timestamp) => new Date(timestamp));
    const tickDates = monthTickDates.length ? monthTickDates : [new Date(minTime)];
    const lines = [
      `<line x1="${margin.left}" y1="${margin.top + innerHeight}" x2="${margin.left + innerWidth}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      ...priceTicks.map((tickPrice) => {
        const y = yScale(tickPrice);
        return `
          <line x1="${margin.left}" y1="${y.toFixed(2)}" x2="${margin.left + innerWidth}" y2="${y.toFixed(2)}" stroke="rgba(148,163,184,0.10)" stroke-width="1" />
          <text x="${margin.left - 8}" y="${(y + 4).toFixed(2)}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="end">${formatPriceAxisLabel(tickPrice, priceAxis.step)}</text>`;
      }),
      ...tickDates.map((tickDate) => {
        const x = xScale(tickDate.getTime());
        return `
          <line x1="${x.toFixed(2)}" y1="${margin.top + innerHeight}" x2="${x.toFixed(2)}" y2="${margin.top + innerHeight + 4}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />
          <text x="${x.toFixed(2)}" y="${height - 10}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="middle">${formatMonthAxisDate(tickDate)}</text>`;
      }),
    ].join("");
    const candlesMarkup = historyCandles
      .map((candle) => {
        const x = xScale(candle.timestamp);
        const wickTop = yScale(candle.high);
        const wickBottom = yScale(candle.low);
        const openY = yScale(candle.open);
        const closeY = yScale(candle.close);
        const bullish = candle.close >= candle.open;
        const fill = bullish ? "#4ade80" : "#f87171";
        const bodyTop = Math.min(openY, closeY);
        const bodyBottom = Math.max(openY, closeY);
        const bodyHeight = Math.max(1.5, bodyBottom - bodyTop);
        const bodyX = x - (bodyWidth / 2);
        return `
          <g>
            <title>${escapeSvgText(formatResearchChartTooltip(candle))}</title>
            <line x1="${x.toFixed(2)}" y1="${wickTop.toFixed(2)}" x2="${x.toFixed(2)}" y2="${wickBottom.toFixed(2)}" stroke="${fill}" stroke-width="1.6" />
            <rect x="${bodyX.toFixed(2)}" y="${bodyTop.toFixed(2)}" width="${bodyWidth.toFixed(2)}" height="${bodyHeight.toFixed(2)}" fill="${fill}" fill-opacity="0.88" stroke="rgba(2,6,23,0.85)" stroke-width="1" rx="1" ry="1" />
          </g>`;
      })
      .join("");
    researchChartPlaceholderEl.innerHTML = `
      <svg class="trade-chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="none" role="img" aria-label="${symbol} ${chartLabel.toLowerCase()} OHLCV chart">
        ${lines}
        ${candlesMarkup}
      </svg>`;
    if (researchChartLoadingEl && researchChartLoadingEl.parentElement) {
      researchChartLoadingEl.parentElement.classList.remove("is-loading");
    }
  }

  function escapeSvgText(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&apos;");
  }

  function ensureTradeMarkerTooltip() {
    if (!tradeHistoryChartEl) {
      return null;
    }
    if (tradeMarkerTooltipEl && tradeMarkerTooltipEl.isConnected) {
      return tradeMarkerTooltipEl;
    }
    tradeMarkerTooltipEl = tradeHistoryChartEl.querySelector("[data-trade-marker-tooltip-box]");
    if (!tradeMarkerTooltipEl) {
      tradeMarkerTooltipEl = document.createElement("div");
      tradeMarkerTooltipEl.className = "trade-marker-tooltip";
      tradeMarkerTooltipEl.dataset.tradeMarkerTooltipBox = "true";
      tradeMarkerTooltipEl.setAttribute("aria-hidden", "true");
      tradeHistoryChartEl.appendChild(tradeMarkerTooltipEl);
    }
    return tradeMarkerTooltipEl;
  }

  function hideTradeMarkerTooltip() {
    const tooltip = ensureTradeMarkerTooltip();
    if (!tooltip) {
      return;
    }
    tooltip.style.display = "none";
    tooltip.textContent = "";
    tooltip.dataset.tradeMarkerTooltipText = "";
  }

  function showTradeMarkerTooltip(text, clientX, clientY) {
    const tooltip = ensureTradeMarkerTooltip();
    if (!tooltip) {
      return;
    }
    const content = String(text || "").trim();
    if (!content) {
      hideTradeMarkerTooltip();
      return;
    }
    const shellRect = tradeHistoryChartEl.getBoundingClientRect();
    tooltip.textContent = content;
    tooltip.style.display = "block";
    tooltip.dataset.tradeMarkerTooltipText = content;
    const tooltipWidth = tooltip.offsetWidth || 0;
    const tooltipHeight = tooltip.offsetHeight || 0;
    const left = Math.min(
      Math.max(8, clientX - shellRect.left - (tooltipWidth / 2)),
      Math.max(8, shellRect.width - tooltipWidth - 8),
    );
    const top = Math.max(8, clientY - shellRect.top - tooltipHeight - 12);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function handleTradeMarkerTooltipEvent(event) {
    if (!tradeHistoryChartEl) {
      return;
    }
    const target = event.target instanceof Element ? event.target.closest("[data-trade-marker-tooltip]") : null;
    if (!target) {
      hideTradeMarkerTooltip();
      return;
    }
    const text = String(target.dataset.tradeMarkerTooltip || "").trim();
    if (!text) {
      hideTradeMarkerTooltip();
      return;
    }
    showTradeMarkerTooltip(text, event.clientX, event.clientY);
  }

  function bindTradeMarkerTooltipEvents() {
    if (!tradeHistoryChartEl || tradeHistoryChartEl.dataset.tradeMarkerTooltipBound === "true") {
      return;
    }
    tradeHistoryChartEl.dataset.tradeMarkerTooltipBound = "true";
    tradeHistoryChartEl.addEventListener("pointermove", handleTradeMarkerTooltipEvent);
    tradeHistoryChartEl.addEventListener("pointerover", handleTradeMarkerTooltipEvent);
    tradeHistoryChartEl.addEventListener("pointerleave", hideTradeMarkerTooltip);
    tradeHistoryChartEl.addEventListener("mouseleave", hideTradeMarkerTooltip);
  }

  function extractPositionSymbol(position) {
    if (!position || typeof position !== "object") {
      return "";
    }
    return String(position.display_symbol || position.symbol || position.ticker?.symbol || "").trim().toUpperCase();
  }

  function extractPositionContractSymbol(position) {
    if (!position || typeof position !== "object") {
      return "";
    }
    return String(position.contract_symbol || position.symbol || position.ticker?.symbol || "").trim().toUpperCase();
  }

  function formatOptionContractLabel(contractSymbol, underlyingSymbol = "") {
    const rawSymbol = String(contractSymbol || "").trim().toUpperCase();
    if (!rawSymbol) {
      return "";
    }
    const underlyingHint = String(underlyingSymbol || "").trim().toUpperCase();
    let contractCode = "";
    let displayUnderlying = "";
    if (rawSymbol.includes(" ")) {
      const parts = rawSymbol.split(" ").filter(Boolean);
      if (parts.length < 2) {
        return rawSymbol;
      }
      displayUnderlying = parts[0];
      contractCode = parts.slice(1).join("").replace(/\s+/g, "");
    } else if (underlyingHint && rawSymbol.startsWith(underlyingHint)) {
      displayUnderlying = underlyingHint;
      contractCode = rawSymbol.slice(underlyingHint.length);
    } else if (rawSymbol.length > 15) {
      displayUnderlying = rawSymbol.slice(0, rawSymbol.length - 15);
      contractCode = rawSymbol.slice(-15);
    } else {
      return rawSymbol;
    }
    if (contractCode.length !== 15) {
      return rawSymbol;
    }
    const expirationCode = contractCode.slice(0, 6);
    const optionType = contractCode.slice(6, 7);
    const strikeCode = contractCode.slice(7);
    if (!/^\d{6}$/.test(expirationCode) || !/^[CP]$/.test(optionType) || !/^\d+$/.test(strikeCode)) {
      return rawSymbol;
    }
    const year = Number(expirationCode.slice(0, 2));
    const month = Number(expirationCode.slice(2, 4));
    const day = Number(expirationCode.slice(4, 6));
    const strike = Number(strikeCode) / 1000;
    if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day) || !Number.isFinite(strike)) {
      return rawSymbol;
    }
    const strikeLabel = strike.toFixed(3).replace(/\.?0+$/, "");
    return `${displayUnderlying || underlyingHint || rawSymbol} ${optionType} ${month}/${day}/${String(year).padStart(2, "0")} $${strikeLabel}`;
  }

  function extractPositionDisplaySymbol(position) {
    if (!position || typeof position !== "object") {
      return "";
    }
    const contractSymbol = extractPositionContractSymbol(position);
    const underlyingHint = String(position.display_symbol || position.underlying_symbol || position.ticker?.symbol || position.symbol || "").trim().toUpperCase();
    const formattedLabel = formatOptionContractLabel(contractSymbol, underlyingHint);
    if (formattedLabel && formattedLabel !== contractSymbol) {
      return formattedLabel;
    }
    return extractPositionSymbol(position);
  }

  function extractCurrentQuotePrice(bootstrap, symbol) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!bootstrap || typeof bootstrap !== "object" || !selectedSymbol) {
      return null;
    }
    const positionRows = Array.isArray(bootstrap.position_rows) ? bootstrap.position_rows : [];
    for (const row of positionRows) {
      if (extractPositionSymbol(row) !== selectedSymbol) {
        continue;
      }
      const price = Number(row.last_price);
      if (Number.isFinite(price) && price > 0) {
        return price;
      }
    }
    const quotes = Array.isArray(bootstrap.quotes) ? bootstrap.quotes : [];
    for (const entry of quotes) {
      if (String(entry?.symbol || "").trim().toUpperCase() !== selectedSymbol) {
        continue;
      }
      const payload = entry && typeof entry === "object" ? entry.payload || {} : {};
      const quote = payload.quote || {};
      const snapshot = payload.snapshot || {};
      const candidates = [
        quote.last,
        quote.last_price,
        quote.price,
        quote.close,
        payload.last_price,
        payload.last,
        payload.price,
        payload.close,
        snapshot.min?.c,
        snapshot.day?.c,
        snapshot.prevDay?.c,
      ];
      for (const candidate of candidates) {
        const numeric = Number(candidate);
        if (Number.isFinite(numeric) && numeric > 0) {
          return numeric;
        }
      }
    }
    return null;
  }

  function extractPositionAverageEntryPrice(bootstrap, symbol) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!bootstrap || typeof bootstrap !== "object" || !selectedSymbol) {
      return null;
    }
    const positionRows = Array.isArray(bootstrap.position_rows) ? bootstrap.position_rows : [];
    for (const row of positionRows) {
      if (extractPositionSymbol(row) !== selectedSymbol) {
        continue;
      }
      const averageCost = Number(row.average_cost ?? row.averageCost ?? row.entry_price ?? row.entryPrice);
      if (Number.isFinite(averageCost) && averageCost > 0) {
        return averageCost;
      }
    }
    return null;
  }

  function summarizeQuoteEntries(quotes) {
    if (!Array.isArray(quotes) || !quotes.length) {
      return "";
    }
    return quotes
      .map((entry) => {
        const symbol = String(entry?.symbol || "").trim().toUpperCase();
        const payload = entry && typeof entry === "object" ? entry.payload || {} : {};
        const price = extractCurrentQuotePrice({ quotes: [entry] }, symbol);
        return `${symbol}:${Number.isFinite(price) ? price : ""}`;
      })
      .filter((entry) => entry.startsWith(":") === false)
      .join(",");
  }

  function extractTradeRows(bootstrap) {
    if (!bootstrap || typeof bootstrap !== "object") {
      return [];
    }
    const orderRows = Array.isArray(bootstrap.order_history_rows) ? bootstrap.order_history_rows : [];
    if (orderRows.length) {
      const normalizedOrderRows = orderRows
        .map((row) => ({
          symbol: String(row.symbol || "").trim().toUpperCase(),
          timestamp: String(row.timestamp || "").trim(),
          price: Number(row.price),
          quantity: Number(row.quantity),
          side: String(row.side || "").trim().toUpperCase(),
          description: String(row.description || "").trim(),
          transaction_id: String(row.transaction_id || "").trim(),
          raw: row.raw || {},
        }))
        .filter((row) => row.symbol && row.timestamp);
      if (normalizedOrderRows.length) {
        return normalizedOrderRows;
      }
    }
    const tradeRows = Array.isArray(bootstrap.trade_history_rows) ? bootstrap.trade_history_rows : [];
    if (tradeRows.length) {
      return tradeRows
        .map((row) => ({
          symbol: String(row.symbol || "").trim().toUpperCase(),
          timestamp: String(row.timestamp || "").trim(),
          price: Number(row.price),
          quantity: Number(row.quantity),
          side: String(row.side || "").trim().toUpperCase(),
          description: String(row.description || "").trim(),
          transaction_id: String(row.transaction_id || "").trim(),
          raw: row.raw || {},
        }))
        .filter((row) => row.symbol && row.timestamp);
    }
    const transactions = Array.isArray(bootstrap.transactions) ? bootstrap.transactions : [];
    return transactions
      .map((transaction) => {
        if (!transaction || typeof transaction !== "object") {
          return null;
        }
        const symbol = String(
          transaction.symbol
            || transaction.underlyingSymbol
            || transaction.instrument?.symbol
            || transaction.transactionItem?.instrument?.symbol
            || "",
        ).trim().toUpperCase();
        const timestamp = String(
          transaction.transactionDateTime
            || transaction.tradeDateTime
            || transaction.transactionDate
            || transaction.tradeDate
            || transaction.date
            || "",
        ).trim();
        if (!symbol || !timestamp) {
          return null;
        }
        const quantity = Number(
          transaction.quantity
            || transaction.shares
            || transaction.longQuantity
            || transaction.shortQuantity
            || transaction.transactionItem?.quantity
            || 0,
        );
        const rawPrice = transaction.price
          || transaction.pricePerShare
          || transaction.netPrice
          || transaction.executionPrice
          || transaction.tradePrice
          || transaction.amount
          || transaction.netAmount
          || transaction.totalAmount
          || null;
        const price = Number.isFinite(Number(rawPrice)) && quantity !== 0
          ? Math.abs(Number(rawPrice)) / Math.abs(quantity)
          : Number(rawPrice);
        return {
          symbol,
          timestamp,
          price: Number.isFinite(price) ? price : null,
          quantity: Math.abs(quantity),
          side: String(transaction.instruction || transaction.side || transaction.activityType || transaction.transactionType || "").trim().toUpperCase(),
          description: String(transaction.description || transaction.transactionDescription || transaction.activityDescription || "").trim(),
          transaction_id: String(transaction.transactionId || transaction.id || "").trim(),
          raw: transaction,
        };
      })
      .filter((row) => row && row.symbol && row.timestamp)
      .sort((left, right) => String(left.timestamp).localeCompare(String(right.timestamp)));
  }

  function extractPriceHistorySeries(bootstrap, symbol) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!bootstrap || typeof bootstrap !== "object" || !selectedSymbol) {
      return null;
    }
    const historyMap = bootstrap.price_history_map && typeof bootstrap.price_history_map === "object" && !Array.isArray(bootstrap.price_history_map)
      ? bootstrap.price_history_map
      : {};
    const directMatch = historyMap[selectedSymbol] || historyMap[selectedSymbol.toLowerCase()] || historyMap[selectedSymbol.toUpperCase()];
    if (directMatch && typeof directMatch === "object") {
      if (directMatch.payload && typeof directMatch.payload === "object") {
        return directMatch.payload;
      }
      return directMatch;
    }
    const historyRows = Array.isArray(bootstrap.price_history_rows) ? bootstrap.price_history_rows : [];
    for (const row of historyRows) {
      if (extractPositionSymbol(row) !== selectedSymbol) {
        continue;
      }
      if (row && typeof row === "object") {
        if (row.payload && typeof row.payload === "object") {
          return row.payload;
        }
        return row;
      }
    }
    return null;
  }

  function extractPriceHistoryUpdatedAt(bootstrap, symbol) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!bootstrap || typeof bootstrap !== "object" || !selectedSymbol) {
      return "";
    }
    const historyMap = bootstrap.price_history_map && typeof bootstrap.price_history_map === "object" && !Array.isArray(bootstrap.price_history_map)
      ? bootstrap.price_history_map
      : {};
    const directMatch = historyMap[selectedSymbol] || historyMap[selectedSymbol.toLowerCase()] || historyMap[selectedSymbol.toUpperCase()];
    if (directMatch && typeof directMatch === "object") {
      const directAsOf = String(directMatch.as_of || directMatch.payload?.as_of || "").trim();
      if (directAsOf) {
        return directAsOf;
      }
    }
    const historyRows = Array.isArray(bootstrap.price_history_rows) ? bootstrap.price_history_rows : [];
    for (const row of historyRows) {
      if (extractPositionSymbol(row) !== selectedSymbol || !row || typeof row !== "object") {
        continue;
      }
      const rowAsOf = String(row.as_of || row.payload?.as_of || "").trim();
      if (rowAsOf) {
        return rowAsOf;
      }
    }
    return "";
  }

  function extractMatchingTradeRows(bootstrap, symbol) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!selectedSymbol) {
      return [];
    }
    const rows = extractTradeRows(bootstrap);
    const directMatches = rows.filter((row) => row.symbol === selectedSymbol);
    if (directMatches.length) {
      return directMatches;
    }
    const fallbackMatches = rows.filter((row) => {
      const description = String(row.description || "").toUpperCase();
      const raw = row.raw && typeof row.raw === "object" ? JSON.stringify(row.raw).toUpperCase() : "";
      return description.includes(selectedSymbol) || raw.includes(`"${selectedSymbol}"`);
    });
    return fallbackMatches;
  }

  function ensurePositionsTable() {
    if (!positionsGridEl) {
      return null;
    }
    const existing = positionsGridEl.querySelector("[data-finance-position-rows]");
    if (existing) {
      positionsTableBodyEl = existing;
      positionsTableFootEl = positionsGridEl.querySelector("[data-finance-position-foot]");
      return existing;
    }
    positionsGridEl.className = "table-shell";
    positionsGridEl.innerHTML = `
      <div class="table-wrap">
        <table class="finance-table" aria-label="Loaded positions">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Shares</th>
              <th>Entry</th>
              <th>Last</th>
              <th>Cost</th>
              <th>Value</th>
              <th>%P</th>
              <th>$</th>
              <th>%</th>
            </tr>
          </thead>
          <tbody data-finance-position-rows></tbody>
          <tfoot data-finance-position-foot></tfoot>
        </table>
      </div>`;
    positionsTableBodyEl = positionsGridEl.querySelector("[data-finance-position-rows]");
    positionsTableFootEl = positionsGridEl.querySelector("[data-finance-position-foot]");
    return positionsTableBodyEl;
  }

  function renderPositionRow(position) {
    const symbol = extractPositionSymbol(position);
    const displaySymbol = extractPositionDisplaySymbol(position) || symbol;
    const contractSymbol = extractPositionContractSymbol(position);
    const row = document.createElement("tr");
    row.dataset.symbol = symbol;
    if (contractSymbol && contractSymbol !== symbol) {
      row.dataset.contractSymbol = contractSymbol;
    }
    row.dataset.costBasis = String(position.cost_basis ?? 0);
    row.dataset.marketValue = String(position.market_value ?? 0);
    row.dataset.commissions = String(position.commissions ?? 0);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    const ariaLabel = contractSymbol && contractSymbol !== symbol
      ? `${symbol} position for contract ${contractSymbol}`
      : symbol;
    row.setAttribute("aria-label", ariaLabel ? `Show 30-day snapshot for ${ariaLabel}` : "Show 30-day snapshot");
    row.addEventListener("click", () => {
      if (symbol) {
        selectPositionSymbol(symbol);
      }
    });
    row.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && symbol) {
        event.preventDefault();
        selectPositionSymbol(symbol);
      }
    });

    const gainPercent = Number(position.gain_percent);
    const gainAmount = Number(position.gain_amount);
    const columns = [
      {
        value: displaySymbol,
        className: "ticker-cell",
        sublabel: contractSymbol && contractSymbol !== displaySymbol ? contractSymbol : "",
      },
      { value: formatNumber(position.quantity_display ?? position.quantity, 0) },
      { value: formatNumber(position.average_cost, 4) },
      {
        value: position.last_price == null ? "-" : formatNumber(position.last_price, 4),
        lastPriceAsOf: String(position.last_price_as_of || "").trim(),
      },
      { value: formatNumber(position.cost_basis, 2) },
      { value: position.market_value == null ? "-" : formatNumber(position.market_value, 2) },
      { value: "-", className: "portfolio-percent-cell" },
      {
        value: formatNumber(gainAmount, 2),
        className: gainAmount > 0 ? "gain-positive" : gainAmount < 0 ? "gain-negative" : "gain-neutral",
      },
      {
        value: formatPercent(gainPercent),
        className: gainPercent > 0 ? "gain-positive" : gainPercent < 0 ? "gain-negative" : "gain-neutral",
      },
    ];

    for (const column of columns) {
      const cell = document.createElement("td");
      if (column.className) {
        cell.className = column.className;
      }
      if (column.sublabel) {
        const wrapper = document.createElement("span");
        wrapper.className = "ticker-cell-stack";
        const primary = document.createElement("span");
        primary.className = "ticker-cell-value";
        primary.textContent = column.value;
        wrapper.appendChild(primary);
        const sublabel = document.createElement("span");
        sublabel.className = "ticker-cell-sublabel";
        sublabel.textContent = column.sublabel;
        wrapper.appendChild(sublabel);
        cell.appendChild(wrapper);
      } else if (column.lastPriceAsOf) {
        cell.classList.add("last-price-cell");
        const wrapper = document.createElement("span");
        wrapper.className = "last-price-stack";
        const price = document.createElement("span");
        price.className = "last-price-value";
        price.textContent = column.value;
        wrapper.appendChild(price);
        const badgeText = formatQuoteCacheBadge(column.lastPriceAsOf);
        if (badgeText) {
          const badge = document.createElement("sup");
          badge.className = "quote-cache-badge";
          badge.textContent = badgeText;
          applyQuoteBadgeStyle(badge, column.lastPriceAsOf);
          const tooltip = formatQuoteCacheTooltip(column.lastPriceAsOf);
          if (tooltip) {
            badge.title = `Quote cache updated ${tooltip}`;
          }
          wrapper.appendChild(badge);
        }
        cell.appendChild(wrapper);
      } else {
        cell.textContent = column.value;
      }
      row.appendChild(cell);
    }
    if (symbol && symbol === selectedPositionSymbol) {
      row.classList.add("is-selected");
    }
    return row;
  }

  function renderPositionTable(rows) {
    const body = ensurePositionsTable();
    if (!body) {
      return;
    }
    body.textContent = "";
    const cashValue = Number(currentBootstrap?.portfolio?.cash);
    const cashAmount = Number.isFinite(cashValue) ? cashValue : 0;
    const marketValueTotal = rows.reduce((total, position) => {
      const marketValue = Number(position?.market_value);
      return Number.isFinite(marketValue) ? total + marketValue : total;
    }, 0);
    const portfolioValueTotal = marketValueTotal + cashAmount;
    const sortedRows = [...rows].sort((left, right) => {
      const leftMarketValue = Number(left?.market_value);
      const rightMarketValue = Number(right?.market_value);
      const leftMarketValueScore = Number.isFinite(leftMarketValue) ? leftMarketValue : Number.NEGATIVE_INFINITY;
      const rightMarketValueScore = Number.isFinite(rightMarketValue) ? rightMarketValue : Number.NEGATIVE_INFINITY;
      if (rightMarketValueScore !== leftMarketValueScore) {
        return rightMarketValueScore - leftMarketValueScore;
      }
      const leftPercent = portfolioValueTotal > 0 && Number.isFinite(leftMarketValue)
        ? 100 * (leftMarketValue / portfolioValueTotal)
        : Number.NEGATIVE_INFINITY;
      const rightPercent = portfolioValueTotal > 0 && Number.isFinite(rightMarketValue)
        ? 100 * (rightMarketValue / portfolioValueTotal)
        : Number.NEGATIVE_INFINITY;
      if (rightPercent !== leftPercent) {
        return rightPercent - leftPercent;
      }
      return String(extractPositionSymbol(left) || "").localeCompare(String(extractPositionSymbol(right) || ""));
    });
    const symbols = sortedRows
      .map((position) => extractPositionSymbol(position))
      .filter(Boolean);
    if (!selectedPositionSymbol || !symbols.includes(selectedPositionSymbol)) {
      selectedPositionSymbol = symbols[0] || "";
    }
    if (!rows.length) {
      const empty = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 9;
      cell.className = "muted-cell";
      cell.textContent = "No positions loaded yet.";
      empty.appendChild(cell);
      body.appendChild(empty);
      syncPositionRowSelection();
      return;
    }
    sortedRows.forEach((position) => {
      body.appendChild(renderPositionRow(position));
    });
    syncPositionRowSelection();
    renderTradeHistoryPanel(currentBootstrap);
  }

  function renderPositionTableFooter(portfolio) {
    if (!positionsTableFootEl) {
      return;
    }
    const rows = Array.from(positionsTableBodyEl?.querySelectorAll("tr") || []);
    let totalCostBasis = 0;
    let totalCommissions = 0;
    let totalMarketValue = 0;
    let hasMarketValue = false;
    for (const row of rows) {
      const cells = row.querySelectorAll("td");
      if (cells.length < 9) {
        continue;
      }
      const costBasis = Number(row.dataset.costBasis || "0");
      const marketValue = Number(row.dataset.marketValue || "0");
      const commissions = Number(row.dataset.commissions || "0");
      if (Number.isFinite(costBasis)) {
        totalCostBasis += costBasis;
      }
      if (Number.isFinite(commissions)) {
        totalCommissions += commissions;
      }
      if (Number.isFinite(marketValue)) {
        totalMarketValue += marketValue;
        hasMarketValue = true;
      }
    }
    const cash = Number(portfolio?.cash);
    const cashDisplay = Number.isFinite(cash) ? formatNumber(cash, 2) : "-";
    const cashValue = Number.isFinite(cash) ? cash : 0;
    const portfolioCostTotal = totalCostBasis + totalCommissions + cashValue;
    const portfolioValueTotal = totalMarketValue + cashValue;
    const gainAmount = hasMarketValue ? portfolioValueTotal - portfolioCostTotal : Number.NaN;
    const gainPercent = hasMarketValue && portfolioCostTotal > 0
      ? 100 * ((portfolioValueTotal / portfolioCostTotal) - 1)
      : Number.NaN;
    for (const row of rows) {
      const marketValue = Number(row.dataset.marketValue || "0");
      const percentCell = row.querySelector(".portfolio-percent-cell");
      if (!percentCell) {
        continue;
      }
      const portfolioPercent = portfolioValueTotal > 0 ? 100 * (marketValue / portfolioValueTotal) : Number.NaN;
      percentCell.textContent = Number.isFinite(portfolioPercent) ? formatPercent(portfolioPercent, 1) : "-";
      percentCell.className = "portfolio-percent-cell";
    }
    positionsTableFootEl.textContent = "";
    const cashRow = document.createElement("tr");
    const cashTickerCell = document.createElement("td");
    cashTickerCell.className = "ticker-cell";
    cashTickerCell.textContent = "$CASH";
    const cashSharesCell = document.createElement("td");
    cashSharesCell.textContent = "";
    const cashEntryCell = document.createElement("td");
    cashEntryCell.textContent = "";
    const cashLastCell = document.createElement("td");
    cashLastCell.textContent = "";
    const cashCostCell = document.createElement("td");
    cashCostCell.textContent = "";
    const cashValueCell = document.createElement("td");
    cashValueCell.className = "";
    cashValueCell.textContent = cashDisplay;
    const cashPercentCell = document.createElement("td");
    cashPercentCell.className = "portfolio-percent-cell";
    cashPercentCell.textContent = portfolioValueTotal > 0 ? formatPercent(100 * (cashValue / portfolioValueTotal), 1) : "-";
    const cashGainAmountCell = document.createElement("td");
    cashGainAmountCell.className = "gain-neutral";
    cashGainAmountCell.textContent = "";
    const cashGainPercentCell = document.createElement("td");
    cashGainPercentCell.className = "gain-neutral";
    cashGainPercentCell.textContent = "";
    cashRow.append(
      cashTickerCell,
      cashSharesCell,
      cashEntryCell,
      cashLastCell,
      cashCostCell,
      cashValueCell,
      cashPercentCell,
      cashGainAmountCell,
      cashGainPercentCell,
    );

    const totalRow = document.createElement("tr");
    const totalLabelCell = document.createElement("td");
    totalLabelCell.colSpan = 4;
    totalLabelCell.className = "muted-cell";
    totalLabelCell.textContent = "Portfolio totals";
    const basisCell = document.createElement("td");
    basisCell.textContent = formatNumber(portfolioCostTotal, 2);
    const marketValueCell = document.createElement("td");
    marketValueCell.textContent = hasMarketValue ? formatNumber(portfolioValueTotal, 2) : "-";
    const portfolioPercentCell = document.createElement("td");
    portfolioPercentCell.textContent = Number.isFinite(portfolioValueTotal) && portfolioValueTotal > 0 ? "100.0%" : "-";
    const gainAmountCell = document.createElement("td");
    gainAmountCell.textContent = Number.isFinite(gainAmount) ? formatNumber(gainAmount, 2) : "-";
    gainAmountCell.className = Number.isFinite(gainAmount)
      ? gainAmount > 0
        ? "gain-positive"
        : gainAmount < 0
          ? "gain-negative"
          : "gain-neutral"
      : "gain-neutral";
    const gainPercentCell = document.createElement("td");
    gainPercentCell.textContent = Number.isFinite(gainPercent) ? `${gainPercent.toFixed(2)}%` : "-";
    gainPercentCell.className = Number.isFinite(gainPercent)
      ? gainPercent > 0
        ? "gain-positive"
        : gainPercent < 0
          ? "gain-negative"
          : "gain-neutral"
      : "gain-neutral";
    totalRow.append(
      totalLabelCell,
      basisCell,
      marketValueCell,
      portfolioPercentCell,
      gainAmountCell,
      gainPercentCell,
    );
    positionsTableFootEl.append(cashRow, totalRow);
  }

  function syncPositionRowSelection() {
    if (!positionsTableBodyEl) {
      return;
    }
    for (const row of positionsTableBodyEl.querySelectorAll("tr[data-symbol]")) {
      row.classList.toggle("is-selected", String(row.dataset.symbol || "") === selectedPositionSymbol);
    }
  }

  function selectPositionSymbol(symbol) {
    const nextSymbol = String(symbol || "").trim().toUpperCase();
    if (!nextSymbol || nextSymbol === selectedPositionSymbol) {
      syncPositionRowSelection();
      renderTradeHistoryPanel(currentBootstrap);
      return;
    }
    selectedPositionSymbol = nextSymbol;
    syncPositionRowSelection();
    renderTradeHistoryPanel(currentBootstrap);
  }

  function formatTradeDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value || "");
    }
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  }

  function formatSnapshotAxisDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value || "");
    }
    const weekday = ["S", "M", "T", "W", "Th", "F", "S"][date.getDay()] || "";
    return `${weekday} ${date.getMonth() + 1}/${date.getDate()}`;
  }

  function formatMonthAxisDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value || "");
    }
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  }

  function formatResearchMonthAxisDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value || "");
    }
    return date.toLocaleDateString("en-US", {
      month: "short",
    });
  }

  function getMonthStartTickIndices(candles) {
    if (!Array.isArray(candles) || !candles.length) {
      return [];
    }
    const indices = [];
    let lastMonthKey = "";
    candles.forEach((candle, index) => {
      const date = new Date(candle.timestamp);
      if (Number.isNaN(date.getTime())) {
        return;
      }
      const monthKey = `${date.getUTCFullYear()}-${date.getUTCMonth()}`;
      if (monthKey !== lastMonthKey) {
        lastMonthKey = monthKey;
        indices.push(index);
      }
    });
    return indices;
  }

  function getMonthBoundaryTicks(startTime, endTime) {
    const start = new Date(startTime);
    const end = new Date(endTime);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
      return [];
    }
    const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
    const ticks = [];
    while (cursor.getTime() <= end.getTime()) {
      ticks.push(cursor.getTime());
      cursor.setUTCMonth(cursor.getUTCMonth() + 1);
    }
    return ticks;
  }

  function getNicePriceAxisStep(minPrice, maxPrice) {
    const range = Math.abs(Number(maxPrice) - Number(minPrice));
    if (!Number.isFinite(range) || range <= 0) {
      return 1;
    }
    const ladder = [0.1, 0.25, 0.5, 1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000];
    for (const step of ladder) {
      if (range / step <= 5) {
        return step;
      }
    }
    return ladder[ladder.length - 1];
  }

  function buildPriceAxisTicks(minPrice, maxPrice) {
    const step = getNicePriceAxisStep(minPrice, maxPrice);
    let axisMin = Math.floor(minPrice / step) * step;
    let axisMax = Math.ceil(maxPrice / step) * step;
    if (!Number.isFinite(axisMin) || !Number.isFinite(axisMax) || axisMin === axisMax) {
      axisMin = minPrice;
      axisMax = maxPrice;
    }
    const ticks = [];
    let value = axisMin;
    for (let safety = 0; safety < 200 && value <= axisMax + (step / 1000); safety += 1, value += step) {
      ticks.push(Number(value.toFixed(step < 1 ? 2 : 0)));
    }
    return { step, axisMin, axisMax, ticks };
  }

  function formatPriceAxisLabel(value, step) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return "";
    }
    const decimals = step < 1 ? 2 : 0;
    return `$${numeric.toLocaleString("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}`;
  }

  function formatSnapshotTooltipQuantity(row) {
    const numeric = Number(row?.quantity);
    if (!Number.isFinite(numeric) || numeric === 0) {
      return "";
    }
    const magnitude = Math.abs(numeric);
    return magnitude % 1 === 0
      ? String(magnitude)
      : magnitude.toLocaleString("en-US", { maximumFractionDigits: 3 });
  }

  function formatSnapshotTooltipTradeLine(row) {
    const quantity = formatSnapshotTooltipQuantity(row);
    const price = Number(row?.price);
    if (!quantity || !Number.isFinite(price) || price <= 0) {
      return "";
    }
    const side = String(row?.side || "").trim().toUpperCase();
    const signedQuantity = side === "SELL" || side === "CLOSE" || side === "SHORT"
      ? `-${quantity}`
      : `+${quantity}`;
    return `${signedQuantity}@${price.toFixed(2)}`;
  }

  function getSnapshotDateKey(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
  }

  function dedupeCandlesByUtcDate(candles) {
    if (!Array.isArray(candles) || !candles.length) {
      return [];
    }
    const map = new Map();
    for (const candle of candles) {
      if (!candle || typeof candle !== "object") {
        continue;
      }
      const key = getSnapshotDateKey(candle.timestamp);
      if (!key) {
        continue;
      }
      map.set(key, candle);
    }
    return Array.from(map.values()).sort((left, right) => Number(left.timestamp) - Number(right.timestamp));
  }

  function formatSnapshotTooltipText(candle, tradeRows) {
    const lines = [
      formatSnapshotAxisDate(candle.timestamp),
      `O: ${formatNumber(candle.open, 2)}`,
      `H: ${formatNumber(candle.high, 2)}`,
      `L: ${formatNumber(candle.low, 2)}`,
      `C: ${formatNumber(candle.close, 2)}`,
    ];
    return lines.join("\n");
  }

  function formatSnapshotOverlayCurrency(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return "-";
    }
    const rounded = numeric.toFixed(2);
    return `${numeric < 0 ? "-" : ""}$${Math.abs(Number(rounded)).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function formatSnapshotOverlayPercent(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return "-";
    }
    const sign = numeric > 0 ? "+" : "";
    return `${sign}${numeric.toFixed(1)}%`;
  }

  function buildSnapshotHoldingsBox(bootstrap, symbol) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!selectedSymbol) {
      return null;
    }
    const positionRows = Array.isArray(bootstrap?.position_rows) ? bootstrap.position_rows : [];
    const row = positionRows.find((entry) => extractPositionSymbol(entry) === selectedSymbol);
    if (!row) {
      return null;
    }
    const quantity = Number(row.quantity);
    const gainPercent = Number(row.gain_percent);
    const gainAmount = Number(row.gain_amount);
    const lines = [];
    if (Number.isFinite(quantity) && quantity > 0) {
      lines.push({
        value: `${quantity.toLocaleString("en-US", { maximumFractionDigits: 3 })} sh`,
        className: "holdings-neutral",
      });
    }
    if (Number.isFinite(gainPercent)) {
      lines.push({
        value: formatSnapshotOverlayPercent(gainPercent),
        className: gainPercent > 0 ? "holdings-positive" : gainPercent < 0 ? "holdings-negative" : "holdings-neutral",
      });
    }
    if (Number.isFinite(gainAmount)) {
      lines.push({
        value: formatSnapshotOverlayCurrency(gainAmount),
        className: gainAmount > 0 ? "holdings-positive" : gainAmount < 0 ? "holdings-negative" : "holdings-neutral",
      });
    }
    if (!lines.length) {
      return null;
    }
    return {
      lines,
      gainAmount,
      gainPercent,
    };
  }

  function renderPriceSnapshotPanel(bootstrap) {
    if (!tradeHistoryChartEl || !tradeHistoryMetaEl) {
      return;
    }
    const positionRows = Array.isArray(bootstrap?.position_rows) ? bootstrap.position_rows : [];
    const fallbackSymbol = selectedPositionSymbol || extractPositionSymbol(positionRows[0]) || "";
    if (!selectedPositionSymbol && fallbackSymbol) {
      selectedPositionSymbol = fallbackSymbol;
    }
    const activeSymbol = selectedPositionSymbol || fallbackSymbol;
    if (!activeSymbol) {
      tradeHistoryMetaEl.textContent = "Select a position to view the 30-day snapshot.";
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">Select a position to view the 30-day snapshot.</div>';
      return;
    }

    const history = extractPriceHistorySeries(bootstrap, activeSymbol);
    const historyAsOf = extractPriceHistoryUpdatedAt(bootstrap, activeSymbol);
    const quotePrice = extractCurrentQuotePrice(bootstrap, activeSymbol);
    const averageEntryPrice = extractPositionAverageEntryPrice(bootstrap, activeSymbol);
    const rawCandles = history && Array.isArray(history.candles)
      ? history.candles
      : history && Array.isArray(history.bars)
        ? history.bars
        : [];
    if (!rawCandles.length) {
      tradeHistoryMetaEl.textContent = `${activeSymbol}: 30-Day Snapshot, no price history loaded yet`;
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">No daily price history is available for this position yet.</div>';
      return;
    }

    const windowEnd = new Date();
    const windowStart = new Date(windowEnd.getTime() - (30 * 24 * 60 * 60 * 1000));
    const candles = rawCandles
      .map((candle) => ({
        timestamp: Number(candle.timestamp ?? candle.datetime ?? candle.time ?? candle.date ?? 0),
        open: Number(candle.open ?? candle.o),
        high: Number(candle.high ?? candle.h),
        low: Number(candle.low ?? candle.l),
        close: Number(candle.close ?? candle.c),
      }))
      .filter((candle) => {
        const time = candle.timestamp;
        return Number.isFinite(time)
          && Number.isFinite(candle.open)
          && Number.isFinite(candle.high)
          && Number.isFinite(candle.low)
          && Number.isFinite(candle.close)
          && time >= windowStart.getTime()
          && time <= windowEnd.getTime();
      })
      .sort((left, right) => left.timestamp - right.timestamp);
    const dedupedCandles = mergeLatestQuoteIntoCandles(dedupeCandlesByUtcDate(candles), quotePrice);
    if (!dedupedCandles.length) {
      tradeHistoryMetaEl.textContent = `${activeSymbol}: 30-Day Snapshot, price history unavailable`;
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">No usable daily candles were found for the last 30 days.</div>';
      return;
    }

    const width = Math.max(320, tradeHistoryChartEl.clientWidth || 520);
    const height = 300;
    const margin = { top: 18, right: 22, bottom: 58, left: 56 };
    const innerWidth = Math.max(1, width - margin.left - margin.right);
    const innerHeight = Math.max(1, height - margin.top - margin.bottom);
    const count = dedupedCandles.length;
    const xScale = (index) => {
      if (count === 1) {
        return margin.left + innerWidth / 2;
      }
      return margin.left + ((index / (count - 1)) * innerWidth);
    };
    const xScaleForTimestamp = (time) => {
      if (count === 1) {
        return margin.left + innerWidth / 2;
      }
      let nearestIndex = 0;
      let nearestDelta = Math.abs(time - dedupedCandles[0].timestamp);
      for (let index = 1; index < dedupedCandles.length; index += 1) {
        const delta = Math.abs(time - dedupedCandles[index].timestamp);
        if (delta < nearestDelta) {
          nearestIndex = index;
          nearestDelta = delta;
        }
      }
      return xScale(nearestIndex);
    };
    const bodyWidth = Math.max(5, Math.min(18, (count > 1 ? innerWidth / (count - 1) : innerWidth) * 0.54));
    const prices = [];
    for (const candle of dedupedCandles) {
      prices.push(candle.high, candle.low);
    }
    if (Number.isFinite(averageEntryPrice) && averageEntryPrice > 0) {
      prices.push(averageEntryPrice);
    }
    let minPrice = Math.min(...prices);
    let maxPrice = Math.max(...prices);
    if (minPrice === maxPrice) {
      const padding = minPrice === 0 ? 1 : Math.abs(minPrice) * 0.1;
      minPrice -= padding;
      maxPrice += padding;
    } else {
      const padding = (maxPrice - minPrice) * 0.1;
      minPrice -= padding;
      maxPrice += padding;
    }
    const yScale = (price) => {
      if (maxPrice === minPrice) {
        return margin.top + innerHeight / 2;
      }
      return margin.top + innerHeight - (((price - minPrice) / (maxPrice - minPrice)) * innerHeight);
    };
    const priceAxis = buildPriceAxisTicks(minPrice, maxPrice);
    minPrice = priceAxis.axisMin;
    maxPrice = priceAxis.axisMax;
    const monthTickIndices = getMonthStartTickIndices(dedupedCandles);
    const uniqueTickIndices = monthTickIndices.length ? monthTickIndices : [0];
    const priceTicks = priceAxis.ticks;
    const quoteLineY = Number.isFinite(quotePrice) && quotePrice > 0 ? yScale(quotePrice) : null;
    const avgLineY = Number.isFinite(averageEntryPrice) && averageEntryPrice > 0 ? yScale(averageEntryPrice) : null;
    const matchingTradeRows = extractMatchingTradeRows(bootstrap, activeSymbol);
    const tradeRowsSource = matchingTradeRows.length ? matchingTradeRows : extractTradeRows(bootstrap);
    const tradeRows = tradeRowsSource
      .map((row) => ({
        timestamp: String(row.timestamp || "").trim(),
        price: Number(row.price),
        quantity: Number(row.quantity),
        side: String(row.side || "").trim().toUpperCase(),
      }))
      .filter((row) => {
        const time = new Date(row.timestamp).getTime();
        return Number.isFinite(time)
          && Number.isFinite(row.price)
          && row.price > 0
          && time >= windowStart.getTime()
          && time <= windowEnd.getTime();
      })
      .sort((left, right) => String(left.timestamp).localeCompare(String(right.timestamp)));
    const holdingsBox = buildSnapshotHoldingsBox(bootstrap, activeSymbol);
    const boxLineHeight = 14;
    const boxPaddingX = 8;
    const boxPaddingY = 8;
    const boxMeasureContext = document.createElement("canvas").getContext("2d");
    if (boxMeasureContext) {
      boxMeasureContext.font = "600 11px Segoe UI, system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
    }
    const boxContentWidth = holdingsBox
      ? Math.max(...holdingsBox.lines.map((line) => {
        const text = String(line?.value || "");
        return boxMeasureContext ? boxMeasureContext.measureText(text).width : text.length * 6;
      }))
      : 0;
    const boxWidth = holdingsBox ? Math.ceil(boxContentWidth) + (boxPaddingX * 2) : 0;
    const boxTextX = holdingsBox
      ? Math.max(margin.left + 10, margin.left + innerWidth - boxWidth - 10)
      : margin.left + 10;
    const boxTextY = margin.top + 16;
    const boxHeight = holdingsBox ? (boxPaddingY * 2) + (holdingsBox.lines.length * boxLineHeight) : 0;
    const quoteTagText = Number.isFinite(quotePrice) && quotePrice > 0 ? formatNumber(quotePrice, 2) : "";
    const quoteTagWidth = quoteTagText
      ? Math.ceil((boxMeasureContext ? boxMeasureContext.measureText(quoteTagText).width : quoteTagText.length * 6) + 16)
      : 0;
    const quoteTagHeight = 22;
    const quoteTagX = 8;
    const quoteTagY = quoteLineY === null
      ? margin.top + 34
      : Math.max(margin.top + 4, Math.min(height - quoteTagHeight - 4, quoteLineY - (quoteTagHeight / 2)));
    const avgTagText = Number.isFinite(averageEntryPrice) && averageEntryPrice > 0 ? formatNumber(averageEntryPrice, 2) : "";
    const avgTagWidth = avgTagText
      ? Math.ceil((boxMeasureContext ? boxMeasureContext.measureText(avgTagText).width : avgTagText.length * 6) + 16)
      : 0;
    const avgTagHeight = 22;
    const avgTagX = 8;
    const avgTagY = avgLineY === null
      ? margin.top + 8
      : Math.max(margin.top + 4, Math.min(height - avgTagHeight - 4, avgLineY - (avgTagHeight / 2)));
    const gridLines = [
      `<line x1="${margin.left}" y1="${margin.top + innerHeight}" x2="${margin.left + innerWidth}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      ...priceTicks.map((tickPrice) => {
        const y = yScale(tickPrice);
        return `
          <line x1="${margin.left}" y1="${y.toFixed(2)}" x2="${margin.left + innerWidth}" y2="${y.toFixed(2)}" stroke="rgba(148,163,184,0.10)" stroke-width="1" />
          <text x="${margin.left - 8}" y="${(y + 4).toFixed(2)}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="end">${formatPriceAxisLabel(tickPrice, priceAxis.step)}</text>`;
      }),
      ...uniqueTickIndices.map((index) => {
        const candle = dedupedCandles[index];
        const x = xScale(index);
        return `
          <line x1="${x.toFixed(2)}" y1="${margin.top + innerHeight}" x2="${x.toFixed(2)}" y2="${margin.top + innerHeight + 4}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />
          <text x="${x.toFixed(2)}" y="${height - 10}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="middle">${formatMonthAxisDate(candle.timestamp)}</text>`;
      }),
      quoteLineY === null ? "" : `<line x1="${margin.left}" y1="${quoteLineY.toFixed(2)}" x2="${margin.left + innerWidth}" y2="${quoteLineY.toFixed(2)}" stroke="rgba(251,146,60,0.92)" stroke-width="1.8" stroke-dasharray="7 5" />`,
      quoteTagText ? `<g aria-hidden="true">
            <rect x="${quoteTagX}" y="${quoteTagY}" width="${quoteTagWidth}" height="${quoteTagHeight}" rx="6" ry="6" fill="rgba(49,46,41,0.90)" stroke="rgba(251,146,60,0.92)" stroke-width="1.2" />
            <text x="${quoteTagX + (quoteTagWidth / 2)}" y="${quoteTagY + 15}" fill="rgba(251,146,60,0.98)" font-size="11" font-weight="600" text-anchor="middle">${quoteTagText}</text>
          </g>` : "",
      avgLineY === null ? "" : `<line x1="${margin.left}" y1="${avgLineY.toFixed(2)}" x2="${margin.left + innerWidth}" y2="${avgLineY.toFixed(2)}" stroke="rgba(248,250,252,0.94)" stroke-width="1.8" stroke-dasharray="7 5" />`,
      avgTagText ? `<g aria-hidden="true">
            <rect x="${avgTagX}" y="${avgTagY}" width="${avgTagWidth}" height="${avgTagHeight}" rx="6" ry="6" fill="rgba(2,6,23,0.34)" stroke="rgba(248,250,252,0.94)" stroke-width="1.2" />
            <text x="${avgTagX + (avgTagWidth / 2)}" y="${avgTagY + 15}" fill="rgba(248,250,252,0.98)" font-size="11" font-weight="600" text-anchor="middle">${avgTagText}</text>
          </g>` : "",
      holdingsBox
        ? `<g aria-hidden="true">
            <rect x="${boxTextX}" y="${margin.top + 8}" width="${boxWidth}" height="${boxHeight}" rx="8" ry="8" fill="rgba(15,23,42,0.86)" stroke="rgba(148,163,184,0.22)" stroke-width="1" />
            ${holdingsBox.lines.map((line, index) => `<text x="${boxTextX + boxPaddingX}" y="${boxTextY + boxPaddingY + (index * boxLineHeight)}" fill="${line.className === "holdings-positive" ? "rgba(74,222,128,0.98)" : line.className === "holdings-negative" ? "rgba(248,113,113,0.98)" : "rgba(226,232,240,0.90)"}" font-size="11" font-weight="600">${line.value}</text>`).join("")}
          </g>`
        : "",
    ].join("");
    const candlesMarkup = dedupedCandles
      .map((candle, index) => {
        const x = xScale(index);
        const wickTop = yScale(candle.high);
        const wickBottom = yScale(candle.low);
        const openY = yScale(candle.open);
        const closeY = yScale(candle.close);
        const bullish = candle.close >= candle.open;
        const fill = bullish ? "#4ade80" : "#f87171";
        const bodyTop = Math.min(openY, closeY);
        const bodyBottom = Math.max(openY, closeY);
        const bodyHeight = Math.max(1.5, bodyBottom - bodyTop);
        const bodyX = x - (bodyWidth / 2);
        const tooltip = formatSnapshotTooltipText(candle);
        return `
          <g>
            <title>${escapeSvgText(tooltip)}</title>
            <line x1="${x.toFixed(2)}" y1="${wickTop.toFixed(2)}" x2="${x.toFixed(2)}" y2="${wickBottom.toFixed(2)}" stroke="${fill}" stroke-width="1.6" />
            <rect x="${bodyX.toFixed(2)}" y="${bodyTop.toFixed(2)}" width="${bodyWidth.toFixed(2)}" height="${bodyHeight.toFixed(2)}" fill="${fill}" fill-opacity="0.88" stroke="rgba(2,6,23,0.85)" stroke-width="1" rx="1" ry="1" />
          </g>`;
      })
      .join("");
    const tradeMarkersMarkup = tradeRows.length
      ? `<g aria-hidden="true">
          ${tradeRows.map((row) => {
            const time = new Date(row.timestamp).getTime();
            if (!Number.isFinite(time)) {
              return "";
            }
            const x = xScaleForTimestamp(time);
            const y = yScale(row.price);
            const isSell = row.side === "SELL" || row.side === "CLOSE";
            const fill = isSell ? "rgba(248,113,113,0.98)" : "rgba(74,222,128,0.98)";
            const markerY = isSell ? y + 14 : y - 14;
            const stemTop = Math.min(y, markerY);
            const stemBottom = Math.max(y, markerY);
            const pointsAttr = isSell
              ? `${(x - 7).toFixed(2)},${(markerY - 5).toFixed(2)} ${(x + 7).toFixed(2)},${(markerY - 5).toFixed(2)} ${x.toFixed(2)},${(markerY + 7).toFixed(2)}`
              : `${(x - 7).toFixed(2)},${(markerY + 5).toFixed(2)} ${(x + 7).toFixed(2)},${(markerY + 5).toFixed(2)} ${x.toFixed(2)},${(markerY - 7).toFixed(2)}`;
            const tooltip = formatSnapshotTooltipTradeLine(row);
            return `
              <g data-trade-marker-tooltip="${escapeSvgText(tooltip)}">
                <line x1="${x.toFixed(2)}" y1="${stemTop.toFixed(2)}" x2="${x.toFixed(2)}" y2="${stemBottom.toFixed(2)}" stroke="rgba(226,232,240,0.85)" stroke-width="2" />
                <polygon points="${pointsAttr}" fill="${fill}" stroke="rgba(2,6,23,0.96)" stroke-width="1.4" />
              </g>`;
          }).join("")}
        </g>`
      : "";
    const historyBadgeMarkup = historyAsOf
      ? `<div class="trade-chart-refresh-badge" data-finance-trade-history-badge title="History cache updated ${formatResearchTimestamp(historyAsOf)}">${formatHistoryRefreshBadge(historyAsOf)}</div>`
      : "";
    tradeHistoryMetaEl.textContent = `${activeSymbol}: 30-Day Snapshot, ${formatTradeDate(windowStart)} to ${formatTradeDate(windowEnd)}`;
    tradeHistoryChartEl.innerHTML = `
      ${historyBadgeMarkup}
      <svg class="trade-chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="none" role="img" aria-label="30-day snapshot chart for ${activeSymbol}">
        ${gridLines}
        ${candlesMarkup}
        ${tradeMarkersMarkup}
      </svg>`;
    ensureTradeMarkerTooltip();
    bindTradeMarkerTooltipEvents();
    hideTradeMarkerTooltip();
  }

  function renderTradeHistoryPanel(bootstrap) {
    if (!tradeHistoryChartEl || !tradeHistoryMetaEl) {
      return;
    }
    renderPriceSnapshotPanel(bootstrap);
    return;
    const rows = extractTradeRows(bootstrap);
    const selectedRows = selectedPositionSymbol
      ? extractMatchingTradeRows(bootstrap, selectedPositionSymbol)
      : rows;
    const fallbackSymbol = selectedRows[0]?.symbol || rows[0]?.symbol || "";
    if (!selectedPositionSymbol && fallbackSymbol) {
      selectedPositionSymbol = fallbackSymbol;
    }
    const visibleRows = selectedPositionSymbol
      ? extractMatchingTradeRows(bootstrap, selectedPositionSymbol)
      : rows;
    const chartTitle = selectedPositionSymbol || fallbackSymbol || "No position selected";
    if (!visibleRows.length) {
      tradeHistoryMetaEl.textContent = chartTitle === "No position selected"
        ? "Select a position to view its trade history."
        : `${chartTitle} · no transactions loaded yet`;
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">No trade activity is available for this position yet.</div>';
      return;
    }

    const points = visibleRows
      .map((row) => ({
        date: new Date(row.timestamp),
        price: Number(row.price),
        side: String(row.side || "").toUpperCase(),
      }))
      .filter((point) => !Number.isNaN(point.date.getTime()) && Number.isFinite(point.price) && point.price > 0)
      .sort((left, right) => left.date.getTime() - right.date.getTime());

    if (!points.length) {
      tradeHistoryMetaEl.textContent = `${chartTitle} · transaction prices unavailable`;
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">No usable execution prices were found for this position yet.</div>';
      return;
    }

    const width = Math.max(320, tradeHistoryChartEl.clientWidth || 0 || 520);
    const height = 280;
    const margin = { top: 18, right: 18, bottom: 58, left: 52 };
    const innerWidth = Math.max(1, width - margin.left - margin.right);
    const innerHeight = Math.max(1, height - margin.top - margin.bottom);
    const minTime = points[0].date.getTime();
    const maxTime = points[points.length - 1].date.getTime();
    const prices = points.map((point) => point.price);
    let minPrice = Math.min(...prices);
    let maxPrice = Math.max(...prices);
    if (minPrice === maxPrice) {
      const padding = minPrice === 0 ? 1 : Math.abs(minPrice) * 0.1;
      minPrice -= padding;
      maxPrice += padding;
    } else {
      const padding = (maxPrice - minPrice) * 0.12;
      minPrice -= padding;
      maxPrice += padding;
    }
    const xScale = (time) => {
      if (maxTime === minTime) {
        return margin.left + innerWidth / 2;
      }
      return margin.left + ((time - minTime) / (maxTime - minTime)) * innerWidth;
    };
    const yScale = (price) => {
      if (maxPrice === minPrice) {
        return margin.top + innerHeight / 2;
      }
      return margin.top + innerHeight - (((price - minPrice) / (maxPrice - minPrice)) * innerHeight);
    };

    const path = points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${xScale(point.date.getTime()).toFixed(2)} ${yScale(point.price).toFixed(2)}`)
      .join(" ");
    const tickCount = Math.min(4, points.length);
    const tickDates = tickCount === 1
      ? [points[0].date]
      : Array.from({ length: tickCount }, (_, index) => {
        const ratio = tickCount === 1 ? 0 : index / (tickCount - 1);
        return new Date(minTime + ((maxTime - minTime) * ratio));
      });
    const priceTicks = Array.from({ length: 4 }, (_, index) => {
      const ratio = index / 3;
      return minPrice + ((maxPrice - minPrice) * ratio);
    });

    const lines = [
      `<line x1="${margin.left}" y1="${margin.top + innerHeight}" x2="${margin.left + innerWidth}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      ...priceTicks.map((tickPrice) => {
        const y = yScale(tickPrice);
        return `
          <line x1="${margin.left}" y1="${y.toFixed(2)}" x2="${margin.left + innerWidth}" y2="${y.toFixed(2)}" stroke="rgba(148,163,184,0.10)" stroke-width="1" />
          <text x="${margin.left - 8}" y="${(y + 4).toFixed(2)}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="end">${formatNumber(tickPrice, 2)}</text>`;
      }),
      ...tickDates.map((tickDate) => {
        const x = xScale(tickDate.getTime());
        return `
          <line x1="${x.toFixed(2)}" y1="${margin.top + innerHeight}" x2="${x.toFixed(2)}" y2="${margin.top + innerHeight + 4}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />
          <text x="${x.toFixed(2)}" y="${height - 10}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="middle">${formatTradeDate(tickDate)}</text>`;
      }),
    ].join("");

    const markers = points
      .map((point) => {
        const x = xScale(point.date.getTime());
        const y = yScale(point.price);
        const fill = point.side === "SELL" ? "#f87171" : "#4ade80";
        const pointsAttr = point.side === "SELL"
          ? `${(x - 6).toFixed(2)},${(y - 4).toFixed(2)} ${(x + 6).toFixed(2)},${(y - 4).toFixed(2)} ${x.toFixed(2)},${(y + 6).toFixed(2)}`
          : `${(x - 6).toFixed(2)},${(y + 4).toFixed(2)} ${(x + 6).toFixed(2)},${(y + 4).toFixed(2)} ${x.toFixed(2)},${(y - 6).toFixed(2)}`;
        const markerDate = `${String(point.date.getMonth() + 1).padStart(2, "0")}/${String(point.date.getDate()).padStart(2, "0")}`;
        const shares = Number.isFinite(point.quantity) && point.quantity > 0
          ? String(Number.isInteger(point.quantity) ? point.quantity : point.quantity.toFixed(3).replace(/\.?0+$/, ""))
          : "";
        return `
          <polygon points="${pointsAttr}" fill="${fill}" stroke="rgba(2,6,23,0.9)" stroke-width="1.2" />
          <text x="${x.toFixed(2)}" y="${(y + 20).toFixed(2)}" fill="rgba(226,232,240,0.88)" font-size="11" text-anchor="middle">${markerDate}</text>
          <text x="${x.toFixed(2)}" y="${(y + 34).toFixed(2)}" fill="rgba(226,232,240,0.88)" font-size="11" text-anchor="middle">${shares}</text>`;
      })
      .join("");

    tradeHistoryMetaEl.textContent = `${chartTitle} · ${points.length} trade events · ${formatTradeDate(points[0].date)} to ${formatTradeDate(points[points.length - 1].date)}`;
    tradeHistoryChartEl.innerHTML = `
      <svg class="trade-chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="none" role="img" aria-label="Trade history chart for ${chartTitle}">
        ${lines}
        <path d="${path}" fill="none" stroke="rgba(56,189,248,0.92)" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" />
        ${markers}
      </svg>`;
  }

  function renderTradeHistoryPanel(bootstrap) {
    if (!tradeHistoryChartEl || !tradeHistoryMetaEl) {
      return;
    }
    const rows = extractTradeRows(bootstrap);
    const selectedRows = selectedPositionSymbol
      ? extractMatchingTradeRows(bootstrap, selectedPositionSymbol)
      : rows;
    const fallbackSymbol = selectedRows[0]?.symbol || rows[0]?.symbol || "";
    if (!selectedPositionSymbol && fallbackSymbol) {
      selectedPositionSymbol = fallbackSymbol;
    }
    const visibleRows = selectedPositionSymbol
      ? extractMatchingTradeRows(bootstrap, selectedPositionSymbol)
      : rows;
    const chartTitle = selectedPositionSymbol || fallbackSymbol || "No position selected";
    renderPriceSnapshotPanel(bootstrap);
    return;
    const quotePrice = extractCurrentQuotePrice(bootstrap, selectedPositionSymbol || fallbackSymbol);
    const averageEntryPrice = extractPositionAverageEntryPrice(bootstrap, selectedPositionSymbol || fallbackSymbol);
    if (!visibleRows.length) {
      tradeHistoryMetaEl.textContent = chartTitle === "No position selected"
        ? "Select a position to view its trade history."
        : `${chartTitle} - no transactions loaded yet`;
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">No trade activity is available for this position yet.</div>';
      return;
    }

    const windowEnd = new Date();
    const windowStart = new Date(windowEnd.getTime() - (30 * 24 * 60 * 60 * 1000));
    const points = visibleRows
      .map((row) => ({
        date: new Date(row.timestamp),
        price: Number(row.price),
        side: String(row.side || "").toUpperCase(),
        quantity: Number(row.quantity),
      }))
      .filter((point) => {
        const time = point.date.getTime();
        return !Number.isNaN(time) && Number.isFinite(point.price) && point.price > 0 && time >= windowStart.getTime() && time <= windowEnd.getTime();
      })
      .sort((left, right) => left.date.getTime() - right.date.getTime());

    if (!points.length) {
      tradeHistoryMetaEl.textContent = `${chartTitle} - transaction prices unavailable`;
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">No usable execution prices were found for this position yet.</div>';
      return;
    }

    const width = Math.max(320, tradeHistoryChartEl.clientWidth || 520);
    const height = 280;
    const margin = { top: 18, right: 18, bottom: 58, left: 52 };
    const innerWidth = Math.max(1, width - margin.left - margin.right);
    const innerHeight = Math.max(1, height - margin.top - margin.bottom);
    const minTime = windowStart.getTime();
    const maxTime = windowEnd.getTime();
    const prices = points.map((point) => point.price);
    if (Number.isFinite(quotePrice) && quotePrice > 0) {
      prices.push(quotePrice);
    }
    if (Number.isFinite(averageEntryPrice) && averageEntryPrice > 0) {
      prices.push(averageEntryPrice);
    }
    let minPrice = Math.min(...prices);
    let maxPrice = Math.max(...prices);
    if (minPrice === maxPrice) {
      const padding = minPrice === 0 ? 1 : Math.abs(minPrice) * 0.1;
      minPrice -= padding;
      maxPrice += padding;
    } else {
      const padding = (maxPrice - minPrice) * 0.12;
      minPrice -= padding;
      maxPrice += padding;
    }
    const xScale = (time) => {
      if (maxTime === minTime) {
        return margin.left + innerWidth / 2;
      }
      return margin.left + ((time - minTime) / (maxTime - minTime)) * innerWidth;
    };
    const yScale = (price) => {
      if (maxPrice === minPrice) {
        return margin.top + innerHeight / 2;
      }
      return margin.top + innerHeight - (((price - minPrice) / (maxPrice - minPrice)) * innerHeight);
    };

    const path = points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${xScale(point.date.getTime()).toFixed(2)} ${yScale(point.price).toFixed(2)}`)
      .join(" ");
    const priceAxis = buildPriceAxisTicks(minPrice, maxPrice);
    minPrice = priceAxis.axisMin;
    maxPrice = priceAxis.axisMax;
    const tickDates = getMonthBoundaryTicks(minTime, maxTime)
      .map((timestamp) => new Date(timestamp));
    if (!tickDates.length) {
      tickDates.push(points[0].date);
    }
    const priceTicks = priceAxis.ticks;
    const quoteLine = Number.isFinite(quotePrice) && quotePrice > 0
      ? `
        <line x1="${margin.left}" y1="${yScale(quotePrice).toFixed(2)}" x2="${margin.left + innerWidth}" y2="${yScale(quotePrice).toFixed(2)}" stroke="rgba(251,191,36,0.92)" stroke-width="1.8" stroke-dasharray="7 5" />`
      : "";
    const avgLine = Number.isFinite(averageEntryPrice) && averageEntryPrice > 0
      ? `
        <line x1="${margin.left}" y1="${yScale(averageEntryPrice).toFixed(2)}" x2="${margin.left + innerWidth}" y2="${yScale(averageEntryPrice).toFixed(2)}" stroke="rgba(74,222,128,0.92)" stroke-width="1.8" stroke-dasharray="7 5" />`
      : "";
    const quoteLineY = Number.isFinite(quotePrice) && quotePrice > 0 ? yScale(quotePrice) : null;
    const avgLineY = Number.isFinite(averageEntryPrice) && averageEntryPrice > 0 ? yScale(averageEntryPrice) : null;
    const labelsTooClose = quoteLineY !== null && avgLineY !== null && Math.abs(quoteLineY - avgLineY) < 22;
    const quoteLabelY = quoteLineY === null
      ? null
      : (labelsTooClose ? Math.max(margin.top + 12, quoteLineY - 10) : Math.max(margin.top + 12, quoteLineY - 6));
    const avgLabelY = avgLineY === null
      ? null
      : (labelsTooClose ? Math.min(height - 14, avgLineY + 14) : Math.max(margin.top + 12, avgLineY - 6));
    const quoteLabel = quoteLabelY === null
      ? ""
      : `<text x="${margin.left + innerWidth - 4}" y="${quoteLabelY.toFixed(2)}" fill="rgba(253,224,71,0.92)" font-size="11" text-anchor="end">Current ${formatNumber(quotePrice, 2)}</text>`;
    const avgLabel = avgLabelY === null
      ? ""
      : `<text x="${margin.left + innerWidth - 4}" y="${avgLabelY.toFixed(2)}" fill="rgba(134,239,172,0.92)" font-size="11" text-anchor="end">Avg ${formatNumber(averageEntryPrice, 2)}</text>`;
    const referenceYs = [quoteLineY, avgLineY, quoteLabelY, avgLabelY]
      .filter((value) => Number.isFinite(value));
    const lines = [
      `<line x1="${margin.left}" y1="${margin.top + innerHeight}" x2="${margin.left + innerWidth}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + innerHeight}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />`,
      ...priceTicks.map((tickPrice) => {
        const y = yScale(tickPrice);
        return `
          <line x1="${margin.left}" y1="${y.toFixed(2)}" x2="${margin.left + innerWidth}" y2="${y.toFixed(2)}" stroke="rgba(148,163,184,0.10)" stroke-width="1" />
          <text x="${margin.left - 8}" y="${(y + 4).toFixed(2)}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="end">${formatPriceAxisLabel(tickPrice, priceAxis.step)}</text>`;
      }),
      ...tickDates.map((tickDate) => {
        const x = xScale(tickDate.getTime());
        return `
          <line x1="${x.toFixed(2)}" y1="${margin.top + innerHeight}" x2="${x.toFixed(2)}" y2="${margin.top + innerHeight + 4}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />
          <text x="${x.toFixed(2)}" y="${height - 10}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="middle">${formatResearchMonthAxisDate(tickDate)}</text>`;
      }),
      quoteLine,
      avgLine,
      quoteLabel,
      avgLabel,
    ].join("");
    const markers = points
      .map((point) => {
        const x = xScale(point.date.getTime());
        const y = yScale(point.price);
        const fill = point.side === "SELL" ? "#f87171" : "#4ade80";
        const pointsAttr = point.side === "SELL"
          ? `${(x - 6).toFixed(2)},${(y - 4).toFixed(2)} ${(x + 6).toFixed(2)},${(y - 4).toFixed(2)} ${x.toFixed(2)},${(y + 6).toFixed(2)}`
          : `${(x - 6).toFixed(2)},${(y + 4).toFixed(2)} ${(x + 6).toFixed(2)},${(y + 4).toFixed(2)} ${x.toFixed(2)},${(y - 6).toFixed(2)}`;
        const markerDate = `${String(point.date.getMonth() + 1).padStart(2, "0")}/${String(point.date.getDate()).padStart(2, "0")}`;
        const shares = Number.isFinite(point.quantity) && point.quantity > 0
          ? String(Number.isInteger(point.quantity) ? point.quantity : point.quantity.toFixed(3).replace(/\.?0+$/, ""))
          : "";
        const priceLabel = Number.isFinite(point.price) && point.price > 0 ? formatNumber(point.price, 2) : "";
        const belowTop = y + 20;
        const belowBottom = y + 34;
        const aboveTop = y - 18;
        const aboveBottom = y - 4;
        const countNearReferences = (top, bottom) => referenceYs.reduce((count, refY) => {
          return count + (refY >= (top - 8) && refY <= (bottom + 8) ? 1 : 0);
        }, 0);
        const useAbove = countNearReferences(belowTop, belowBottom) > countNearReferences(aboveTop, aboveBottom);
        const dateY = useAbove ? aboveTop : belowTop;
        const sharesY = useAbove ? aboveBottom : belowBottom;
        return `
          <polygon points="${pointsAttr}" fill="${fill}" stroke="rgba(2,6,23,0.9)" stroke-width="1.2" />
          <text x="${x.toFixed(2)}" y="${dateY.toFixed(2)}" fill="rgba(226,232,240,0.88)" font-size="11" text-anchor="middle">${markerDate}</text>
          <text x="${x.toFixed(2)}" y="${sharesY.toFixed(2)}" fill="rgba(226,232,240,0.88)" font-size="11" text-anchor="middle">${shares}@${priceLabel}</text>`;
      })
      .join("");

    tradeHistoryMetaEl.textContent = `${chartTitle} - ${points.length} trade events in the last 30 days - ${formatTradeDate(windowStart)} to ${formatTradeDate(windowEnd)}`;
    tradeHistoryChartEl.innerHTML = `
      <svg class="trade-chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="none" role="img" aria-label="Trade history chart for ${chartTitle} over the last 30 days">
        ${lines}
        <path d="${path}" fill="none" stroke="rgba(56,189,248,0.92)" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" />
        ${markers}
      </svg>`;
  }

  function buildSnapshotSignature(bootstrap) {
    if (!bootstrap || typeof bootstrap !== "object") {
      return "";
    }
    const portfolio = bootstrap.portfolio || {};
    const quotes = Array.isArray(bootstrap.quotes) ? bootstrap.quotes : [];
    const positions = Array.isArray(bootstrap.positions) ? bootstrap.positions : [];
    const positionRows = Array.isArray(bootstrap.position_rows) ? bootstrap.position_rows : [];
    const priceHistoryRows = Array.isArray(bootstrap.price_history_rows) ? bootstrap.price_history_rows : [];
    const transactions = Array.isArray(bootstrap.transactions) ? bootstrap.transactions : [];
    const orders = Array.isArray(bootstrap.orders) ? bootstrap.orders : [];
    const tradeRows = Array.isArray(bootstrap.trade_history_rows) ? bootstrap.trade_history_rows : [];
    const orderRows = Array.isArray(bootstrap.order_history_rows) ? bootstrap.order_history_rows : [];
    const quoteRows = summarizeQuoteEntries(quotes);
    return [
      String(bootstrap.quote_status || ""),
      String(portfolio.position_count || positions.length || 0),
      String(quotes.length),
      String(positions.length),
      String(transactions.length),
      String(orders.length),
      String(tradeRows.length),
      String(orderRows.length),
      String(portfolio.cash || 0),
      quoteRows,
      positionRows.map((row) => `${String(row.symbol || "")}:${String(row.last_price ?? "")}:${String(row.market_value ?? "")}`).join(","),
      priceHistoryRows.map((row) => {
        const symbol = String(row?.symbol || "").trim().toUpperCase();
        const payload = row && typeof row === "object" ? row.payload || {} : {};
        const candles = Array.isArray(payload.candles) ? payload.candles : Array.isArray(payload.bars) ? payload.bars : [];
        const lastCandle = candles.length ? candles[candles.length - 1] : {};
        return `${symbol}:${candles.length}:${String(lastCandle.timestamp || lastCandle.datetime || "")}:${String(lastCandle.close ?? lastCandle.c ?? "")}`;
      }).join(","),
      tradeRows.slice(0, 8).map((row) => `${String(row.symbol || "")}:${String(row.timestamp || "")}:${String(row.price ?? "")}`).join(","),
      orderRows.slice(0, 8).map((row) => `${String(row.symbol || "")}:${String(row.timestamp || "")}:${String(row.price ?? "")}`).join(","),
    ].join("|");
  }

  function renderBootstrapSnapshot(bootstrap) {
    if (!bootstrap || typeof bootstrap !== "object") {
      return;
    }
    currentBootstrap = bootstrap;
    const portfolio = bootstrap.portfolio || {};
    const quotes = Array.isArray(bootstrap.quotes) ? bootstrap.quotes : [];
    const positions = Array.isArray(bootstrap.positions) ? bootstrap.positions : [];
    const positionRows = Array.isArray(bootstrap.position_rows) ? bootstrap.position_rows : [];
    const transactions = Array.isArray(bootstrap.transactions) ? bootstrap.transactions : [];
    const orders = Array.isArray(bootstrap.orders) ? bootstrap.orders : [];

    if (workspaceNameEl && bootstrap.workspace_name) {
      workspaceNameEl.textContent = String(bootstrap.workspace_name);
    }
    if (positionCountEl) {
      positionCountEl.textContent = `${Number(portfolio.position_count || positions.length || 0)} core holdings`;
    }
    if (quoteCountEl) {
      quoteCountEl.textContent = `${quotes.length} cached or live quotes`;
    }
    if (transactionCountEl) {
      transactionCountEl.textContent = `${Math.max(transactions.length, orders.length)} loaded transactions`;
    }
    if (quoteStatusEl && bootstrap.quote_status) {
      quoteStatusEl.textContent = String(bootstrap.quote_status);
    }
    if (positionsGridEl) {
      renderPositionTable(positionRows.length ? positionRows : positions);
      renderPositionTableFooter(portfolio);
    }
    renderTradeHistoryPanel(bootstrap);

    lastSnapshotSignature = buildSnapshotSignature(bootstrap);
  }

  function updateNote() {
    if (!noteEl) {
      return;
    }
    if (autoFetchEnabled) {
      noteEl.textContent = "";
      return;
    }
    noteEl.textContent = "Use Refresh now for a background update.";
  }

  async function loadTickerResearch(symbol, options = {}) {
    const selectedSymbol = String(symbol || "").trim().toUpperCase();
    if (!selectedSymbol || !researchUrl) {
      return false;
    }
    const queueRefresh = options.queueRefresh !== false;
    const requestId = tickerResearchRequestId + 1;
    tickerResearchRequestId = requestId;
    log("ticker research request start", {
      symbol: selectedSymbol,
      queueRefresh,
      requestId,
      pollAttempt: options.attempt || 0,
      pendingSummaries: researchSourceSummaryPending.size,
    });
    try {
      const response = await fetch(`${researchUrl}?symbol=${encodeURIComponent(selectedSymbol)}&queue=${queueRefresh ? 1 : 0}`, {
        method: "GET",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const payload = await response.json().catch(() => ({}));
      if (requestId !== tickerResearchRequestId) {
        return false;
      }
      if (!response.ok || !payload.ok) {
        warn("ticker research lookup failed", response.status, payload);
        return false;
      }
      const filingsSummaryKeys = payload?.context?.filings_cache?.payload?.ai_summaries && typeof payload.context.filings_cache.payload.ai_summaries === "object"
        ? Object.keys(payload.context.filings_cache.payload.ai_summaries)
        : [];
      log("ticker research response", {
        symbol: selectedSymbol,
        requestId,
        filingsSummaryCount: filingsSummaryKeys.length,
        filingsSummaryKeys: filingsSummaryKeys.slice(0, 4),
        contextReady: isResearchContextReady(payload.context || {}),
      });
      renderResearchContext(payload.context || {});
      if (searchInputEl) {
        searchInputEl.value = selectedSymbol;
      }
      const portfolioSymbols = Array.isArray(currentBootstrap?.positions)
        ? currentBootstrap.positions.map((position) => extractPositionSymbol(position))
        : [];
      if (portfolioSymbols.includes(selectedSymbol)) {
        selectedPositionSymbol = selectedSymbol;
        renderBootstrapSnapshot(currentBootstrap || {});
      }
      lastTickerResearchSymbol = selectedSymbol;
      if (queueRefresh) {
        const ready = isResearchContextReady(payload.context || {});
        if (!ready || hasPendingResearchSourceSummaries()) {
          scheduleTickerResearchPoll(selectedSymbol);
        } else {
          clearTickerResearchPoll();
        }
      } else {
        const ready = isResearchContextReady(payload.context || {});
        if ((!ready || hasPendingResearchSourceSummaries()) && options.scheduleRetry !== false) {
          scheduleTickerResearchPoll(selectedSymbol);
        } else if (ready && !hasPendingResearchSourceSummaries()) {
          clearTickerResearchPoll();
        }
      }
      return true;
    } catch (error) {
      warn("ticker research lookup request failed", error);
      if (queueRefresh && options.scheduleRetry !== false) {
        scheduleTickerResearchPoll(selectedSymbol);
      }
      return false;
    }
  }

  async function fetchTickerSearch(query) {
    const term = String(query || "").trim();
    if (!searchUrl || term.length < 1) {
      clearTickerSearchResults();
      return [];
    }
    const requestId = tickerSearchRequestId + 1;
    tickerSearchRequestId = requestId;
    try {
      const response = await fetch(`${searchUrl}?query=${encodeURIComponent(term)}&limit=10`, {
        method: "GET",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const payload = await response.json().catch(() => ({}));
      if (requestId !== tickerSearchRequestId) {
        return [];
      }
      if (!response.ok || !payload.ok) {
        warn("ticker search failed", response.status, payload);
        clearTickerSearchResults();
        return [];
      }
      const matches = Array.isArray(payload.matches) ? payload.matches : [];
      renderTickerSearchResults(matches);
      if (matches.length === 1) {
        const onlyMatch = matches[0];
        const symbol = String(onlyMatch?.symbol || "").trim().toUpperCase();
        if (symbol) {
          renderResearchContext({
            symbol,
            status: "lookup",
            ticker: onlyMatch,
            quote_cache: null,
            history_cache: null,
            research_snapshot: null,
          });
        }
      }
      return matches;
    } catch (error) {
      warn("ticker search request failed", error);
      clearTickerSearchResults();
      return [];
    }
  }

  function scheduleTickerSearch(query) {
    if (tickerSearchTimer !== null) {
      window.clearTimeout(tickerSearchTimer);
      tickerSearchTimer = null;
    }
    const term = String(query || "").trim();
    if (!term) {
      clearTickerSearchResults();
      return;
    }
    tickerSearchTimer = window.setTimeout(() => {
      tickerSearchTimer = null;
      void fetchTickerSearch(term);
    }, 180);
  }

  function selectTickerFromSearch(match) {
    const symbol = String(match?.symbol || "").trim().toUpperCase();
    if (!symbol) {
      warn("ticker search selection missing symbol", match);
      return;
    }
    if (searchInputEl) {
      searchInputEl.value = symbol;
    }
    clearTickerSearchResults();
    setActiveFinanceTab("research");
    try {
      renderResearchContext({
        symbol,
        status: "lookup",
        ticker: match,
        quote_cache: null,
        history_cache: null,
        research_snapshot: null,
      });
    } catch (error) {
      warn("ticker search selection render failed", error, match);
    }
    clearTickerResearchPoll();
    void loadTickerResearch(symbol, { queueRefresh: true, scheduleRetry: true });
  }

  function syncAutoFetchToggle() {
    if (!autoFetchToggle) {
      return;
    }
    autoFetchToggle.checked = autoFetchEnabled;
    autoFetchToggle.addEventListener("change", () => {
      autoFetchEnabled = !!autoFetchToggle.checked;
      saveAutoFetchPreference(autoFetchEnabled);
      updateNote();
      if (autoFetchEnabled) {
        void triggerRefresh(false);
      }
    });
  }

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const nextTab = String(button.dataset.financeTabButton || "portfolio").trim() || "portfolio";
      setActiveFinanceTab(nextTab);
    });
  });

  researchChartTimeframeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const nextTimeframe = String(button.dataset.financeResearchChartTimeframe || "daily").trim() || "daily";
      setResearchChartTimeframe(nextTimeframe);
    });
  });
  researchViewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const nextView = String(button.dataset.financeResearchViewButton || "dashboard").trim() || "dashboard";
      setResearchView(nextView);
    });
  });
  syncResearchViewControls();
  syncResearchChartControls();

  searchInputEl?.addEventListener("input", () => {
    if (String(searchInputEl.value || "").trim()) {
      setActiveFinanceTab("research");
    }
    scheduleTickerSearch(searchInputEl.value);
  });

  searchInputEl?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      clearTickerSearchResults();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const nextMatch = lastTickerSearchMatches[0];
      if (nextMatch) {
        void selectTickerFromSearch(nextMatch);
      }
    }
  });

  shell.ownerDocument.addEventListener("pointerdown", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }
    const withinSearch = target.closest("[data-finance-search-shell]");
    if (!withinSearch) {
      clearTickerSearchResults();
    }
  });

  shell.ownerDocument.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }
    const button = target.closest("[data-finance-ai-summary-button]");
    if (!button) {
      return;
    }
    event.preventDefault();
    log("AI summary button clicked", {
      sourceUrl: String(button.dataset.financeAiSummarySourceUrl || "").trim(),
      parentCacheKey: String(button.dataset.financeAiSummaryParentCacheKey || "").trim(),
      sourceKind: String(button.dataset.financeAiSummarySourceKind || "").trim(),
    });
    void requestResearchSourceSummary(button);
  });

  function sendPayload(payload) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    socket.send(JSON.stringify(payload));
    return true;
  }

  function sendBootstrapPayload() {
    if (!socket || socket.readyState !== WebSocket.OPEN || bootstrapSent) {
      return bootstrapSent;
    }
    const bootstrapPayload = {
      type: "finance.bootstrap",
      context: String(readJsonScript(financeContextScript) || ""),
      auto_fetch_enabled: autoFetchEnabled,
    };
    socket.send(JSON.stringify(bootstrapPayload));
    bootstrapSent = true;
    return true;
  }

  async function triggerRefresh(announce) {
    if (!refreshUrl) {
      return false;
    }
    setStatus("Refreshing...");
    try {
      const response = await fetch(refreshUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify({ auto_fetch_enabled: autoFetchEnabled }),
      });
      await response.json().catch(() => ({}));
      if (!response.ok) {
        setStatus("Refresh failed");
        return false;
      }
      setStatus(autoFetchEnabled ? "Auto-fetch queued" : "Refresh queued");
      log("refresh queued", { autoFetchEnabled, announce });
      scheduleSnapshotPoll();
      return true;
    } catch (error) {
      setStatus("Refresh failed");
      warn("refresh request failed", error);
      return false;
    }
  }

  function stopSnapshotPoll() {
    if (snapshotPollTimer !== null) {
      window.clearTimeout(snapshotPollTimer);
      snapshotPollTimer = null;
    }
    snapshotPollAttempts = 0;
  }

  function stopSnapshotWatch() {
    if (snapshotWatchTimer !== null) {
      window.clearInterval(snapshotWatchTimer);
      snapshotWatchTimer = null;
    }
    snapshotWatchBusy = false;
  }

  function startSnapshotWatch() {
    if (!stateUrl || snapshotWatchTimer !== null) {
      return;
    }
    snapshotWatchTimer = window.setInterval(() => {
      if (snapshotWatchBusy) {
        return;
      }
      snapshotWatchBusy = true;
      void syncBootstrapSnapshot().finally(() => {
        snapshotWatchBusy = false;
      });
    }, quoteWatchIntervalMs);
  }

  function scheduleSnapshotPoll() {
    stopSnapshotPoll();
    snapshotPollTimer = window.setTimeout(() => {
      void pollBootstrapSnapshot();
    }, 1800);
  }

  async function pollBootstrapSnapshot() {
    if (!stateUrl) {
      return false;
    }
    const previousSignature = lastSnapshotSignature;
    const updated = await syncBootstrapSnapshot();
    if (!updated) {
      return false;
    }
    if (lastSnapshotSignature !== previousSignature) {
      stopSnapshotPoll();
      return true;
    }
    snapshotPollAttempts += 1;
    if (snapshotPollAttempts >= 6) {
      stopSnapshotPoll();
      return true;
    }
    snapshotPollTimer = window.setTimeout(() => {
      void pollBootstrapSnapshot();
    }, 2000);
    return true;
  }

  async function syncBootstrapSnapshot() {
    if (!stateUrl) {
      return false;
    }
    try {
      const response = await fetch(stateUrl, {
        method: "GET",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        warn("state sync failed", response.status, payload);
        return false;
      }
      renderBootstrapSnapshot(payload.bootstrap || {});
      log("state sync", {
        quote_status: payload.bootstrap?.quote_status,
        positions: Array.isArray(payload.bootstrap?.positions) ? payload.bootstrap.positions.length : 0,
        position_rows: Array.isArray(payload.bootstrap?.position_rows) ? payload.bootstrap.position_rows.length : 0,
        quotes: Array.isArray(payload.bootstrap?.quotes) ? payload.bootstrap.quotes.length : 0,
        transactions: Array.isArray(payload.bootstrap?.transactions) ? payload.bootstrap.transactions.length : 0,
      });
      return true;
    } catch {
      warn("state sync request failed");
      return false;
    }
  }

  function connect() {
    if (!wsUrl || !agentSlug) {
      setStatus("Finance agent not configured");
      updateNote();
      return false;
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
      return true;
    }
    if (connecting) {
      return true;
    }

    connecting = true;
    bootstrapSent = false;
    setStatus("Connecting...");

    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}${wsUrl}`;
    socket = new WebSocket(url);

    socket.addEventListener("open", () => {
      connecting = false;
      setStatus("Connected");
      updateNote();
      log("ws open", { agentSlug, wsUrl });
      sendBootstrapPayload();
      if (pendingPrompt) {
        const nextPrompt = pendingPrompt;
        pendingPrompt = "";
        sendPayload({ type: "chat.message", text: nextPrompt });
      }
    });

    socket.addEventListener("close", () => {
      connecting = false;
      bootstrapSent = false;
      setStatus("Disconnected");
      log("ws close");
    });

    socket.addEventListener("error", () => {
      connecting = false;
      setStatus("Connection error");
      warn("ws error");
    });

    socket.addEventListener("message", (event) => {
      let payload = null;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }

      const type = String(payload.type || "").trim();
      if (type === "message") {
        const role = String(payload.role || "assistant").toLowerCase();
        if (role === "user" || role === "operator") {
          const echoedText = String(payload.text || "");
          if (echoedText && echoedText === lastLocalPrompt) {
            lastLocalPrompt = "";
            return;
          }
          appendMessage("user", "You", String(payload.text || ""));
          return;
        }
        if (role !== "assistant") {
          return;
        }
        appendMessage("assistant", "Assistant", String(payload.text || ""));
        return;
      }

      if (type === "error") {
        setStatus(String(payload.text || payload.message || "Error"));
      }
    });

    return true;
  }

  async function queueRefreshFromButton() {
    if (!refreshUrl) {
      return;
    }
    await triggerRefresh(true);
  }

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = String(inputEl?.value || "").trim();
    if (!text) {
      return;
    }
    if (!agentSlug || !wsUrl) {
      setStatus("Finance agent not configured");
      return;
    }
    if (!connect()) {
      return;
    }
    appendMessage("user", "You", text);
    lastLocalPrompt = text;
    log("prompt sent", { length: text.length });
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      pendingPrompt = text;
      if (inputEl) {
        inputEl.value = "";
      }
      return;
    }
    if (!bootstrapSent) {
      sendBootstrapPayload();
    }
    if (!sendPayload({ type: "chat.message", text })) {
      pendingPrompt = text;
    }
    if (inputEl) {
      inputEl.value = "";
    }
  });

  inputEl?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form?.requestSubmit?.();
    }
  });

  refreshButton?.addEventListener("click", () => {
    void queueRefreshFromButton();
  });

  const initialBootstrap = readJsonScript(financeBootstrapScript);
  if (initialBootstrap) {
    renderBootstrapSnapshot(initialBootstrap);
  }
  setActiveFinanceTab("portfolio");
  clearTickerSearchResults();

  syncAutoFetchToggle();
  updateNote();
  startSnapshotWatch();

  if (autoFetchEnabled) {
    void triggerRefresh(false);
  }
})();

