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
  let selectedPositionSymbol = "";
  let tradeMarkerTooltipEl = null;

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
    return date.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
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
    return String(position.symbol || position.ticker?.symbol || "").trim().toUpperCase();
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
    const row = document.createElement("tr");
    row.dataset.symbol = symbol;
    row.dataset.costBasis = String(position.cost_basis ?? 0);
    row.dataset.marketValue = String(position.market_value ?? 0);
    row.dataset.commissions = String(position.commissions ?? 0);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", symbol ? `Show 30-day snapshot for ${symbol}` : "Show 30-day snapshot");
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
      { value: symbol, className: "ticker-cell" },
      { value: formatNumber(position.quantity, 0) },
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
      if (column.lastPriceAsOf) {
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
    if (!candles.length) {
      tradeHistoryMetaEl.textContent = `${activeSymbol}: 30-Day Snapshot, price history unavailable`;
      tradeHistoryChartEl.innerHTML = '<div class="trade-chart-empty">No usable daily candles were found for the last 30 days.</div>';
      return;
    }

    const quotePrice = extractCurrentQuotePrice(bootstrap, activeSymbol);
    const averageEntryPrice = extractPositionAverageEntryPrice(bootstrap, activeSymbol);
    const width = Math.max(320, tradeHistoryChartEl.clientWidth || 520);
    const height = 300;
    const margin = { top: 18, right: 22, bottom: 58, left: 56 };
    const innerWidth = Math.max(1, width - margin.left - margin.right);
    const innerHeight = Math.max(1, height - margin.top - margin.bottom);
    const count = candles.length;
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
      let nearestDelta = Math.abs(time - candles[0].timestamp);
      for (let index = 1; index < candles.length; index += 1) {
        const delta = Math.abs(time - candles[index].timestamp);
        if (delta < nearestDelta) {
          nearestIndex = index;
          nearestDelta = delta;
        }
      }
      return xScale(nearestIndex);
    };
    const bodyWidth = Math.max(5, Math.min(18, (count > 1 ? innerWidth / (count - 1) : innerWidth) * 0.54));
    const prices = [];
    for (const candle of candles) {
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
    const mondayTickIndices = candles
      .map((candle, index) => ({ candle, index }))
      .filter(({ candle }) => {
        const date = new Date(candle.timestamp);
        return !Number.isNaN(date.getTime()) && date.getDay() === 1;
      })
      .map(({ index }) => index);
    const uniqueTickIndices = mondayTickIndices.length ? mondayTickIndices : [0];
    const priceTicks = Array.from({ length: 4 }, (_, index) => minPrice + ((maxPrice - minPrice) * (index / 3)));
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
          <text x="${margin.left - 8}" y="${(y + 4).toFixed(2)}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="end">${formatNumber(tickPrice, 2)}</text>`;
      }),
      ...uniqueTickIndices.map((index) => {
        const candle = candles[index];
        const x = xScale(index);
        return `
          <line x1="${x.toFixed(2)}" y1="${margin.top + innerHeight}" x2="${x.toFixed(2)}" y2="${margin.top + innerHeight + 4}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />
          <text x="${x.toFixed(2)}" y="${height - 10}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="middle">${formatSnapshotAxisDate(candle.timestamp)}</text>`;
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
    const candlesMarkup = candles
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
    tradeHistoryMetaEl.textContent = `${activeSymbol}: 30-Day Snapshot, ${formatTradeDate(windowStart)} to ${formatTradeDate(windowEnd)}`;
    tradeHistoryChartEl.innerHTML = `
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
    const tickDates = Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4;
      return new Date(minTime + ((maxTime - minTime) * ratio));
    });
    const priceTicks = Array.from({ length: 4 }, (_, index) => {
      const ratio = index / 3;
      return minPrice + ((maxPrice - minPrice) * ratio);
    });
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
          <text x="${margin.left - 8}" y="${(y + 4).toFixed(2)}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="end">${formatNumber(tickPrice, 2)}</text>`;
      }),
      ...tickDates.map((tickDate) => {
        const x = xScale(tickDate.getTime());
        return `
          <line x1="${x.toFixed(2)}" y1="${margin.top + innerHeight}" x2="${x.toFixed(2)}" y2="${margin.top + innerHeight + 4}" stroke="rgba(148,163,184,0.24)" stroke-width="1" />
          <text x="${x.toFixed(2)}" y="${height - 10}" fill="rgba(226,232,240,0.74)" font-size="11" text-anchor="middle">${formatTradeDate(tickDate)}</text>`;
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

  syncAutoFetchToggle();
  updateNote();
  startSnapshotWatch();

  if (autoFetchEnabled) {
    void triggerRefresh(false);
  }
})();

