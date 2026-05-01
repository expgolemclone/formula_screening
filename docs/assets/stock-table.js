/**
 * Generic stock table renderer.
 *
 * Initialise via `StockTable.init(config)` after the DOM is ready.
 * Each page supplies a config object that declares columns, data
 * sources, metric thresholds, and optional tab/position behaviour.
 */
/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */
const ASC_ARROW = "\u25B2";
const DESC_ARROW = "\u25BC";
const INACTIVE_ARROW = "\u25BD";
const HIDDEN_COLUMNS_KEY = "hiddenColumns";
/* ------------------------------------------------------------------ */
/*  State                                                              */
/* ------------------------------------------------------------------ */
let _config = null;
const _state = {
    rows: null,
    currentTab: "",
    sortKey: "",
    sortDir: "asc",
    hiddenCols: new Set(),
    loading: true,
    error: "",
};
const _el = {
    tabBar: null,
    status: null,
    thead: null,
    tbody: null,
    toggleBar: null,
};
/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */
export const StockTable = { init };
/* ------------------------------------------------------------------ */
/*  Initialisation                                                     */
/* ------------------------------------------------------------------ */
function init(config) {
    _config = config;
    _state.sortKey = config.defaultSortKey;
    _state.sortDir = config.defaultSortDirection;
    _state.hiddenCols = _loadHiddenCols();
    _el.tabBar = document.getElementById("tabBar");
    _el.status = document.getElementById("statusMessage");
    _el.thead = document.querySelector("#stockTable > thead");
    _el.tbody = document.getElementById("tbody");
    _el.toggleBar = document.getElementById("toggleBar");
    _renderHead();
    _renderToggleChips();
    _bindEvents();
    _render();
    void _loadData();
}
/* ------------------------------------------------------------------ */
/*  Data loading                                                       */
/* ------------------------------------------------------------------ */
async function _loadData() {
    if (!_config) {
        return;
    }
    try {
        const response = await fetch(_config.dataUrl, { cache: "no-store" });
        if (!response.ok) {
            throw new Error("HTTP " + response.status);
        }
        const raw = await response.json();
        _state.rows = _normalizeRows(raw);
        _state.loading = false;
        _state.error = "";
        if (_config.tabMode && _state.rows.length > 0 && _state.rows[0]._tabKey !== undefined) {
            _state.currentTab = _resolveDefaultTab(_state.rows);
        }
        _render();
    }
    catch (err) {
        console.error(err);
        _state.loading = false;
        _state.error = "データを読み込めませんでした。";
        _render();
    }
}
/* ------------------------------------------------------------------ */
/*  Normalisation                                                      */
/* ------------------------------------------------------------------ */
/**
 * Accepts two shapes:
 *   1) Flat array  →  [{ code, name, … }, …]
 *   2) Tab object  →  { watch: { name, stocks: […] }, … }
 *
 * Tab rows receive a `_tabKey` property so the renderer can filter.
 */
function _normalizeRows(raw) {
    if (Array.isArray(raw)) {
        return raw;
    }
    if (raw && typeof raw === "object") {
        const result = [];
        for (const [tabKey, ds] of Object.entries(raw)) {
            const dataset = ds;
            if (!dataset || !Array.isArray(dataset.stocks)) {
                continue;
            }
            for (const stock of dataset.stocks) {
                const row = stock;
                row._tabKey = tabKey;
                row._tabName = dataset.name || tabKey;
                result.push(row);
            }
        }
        return result;
    }
    return [];
}
function _resolveDefaultTab(rows) {
    if (!_config) {
        return "";
    }
    const keys = [];
    const seen = {};
    for (const r of rows) {
        if (r._tabKey && !seen[r._tabKey]) {
            seen[r._tabKey] = true;
            keys.push(r._tabKey);
        }
    }
    if (_config.defaultTabKey && seen[_config.defaultTabKey]) {
        return _config.defaultTabKey;
    }
    return keys.length > 0 ? keys[0] : "";
}
/* ------------------------------------------------------------------ */
/*  Rendering                                                          */
/* ------------------------------------------------------------------ */
function _render() {
    _renderTabs();
    _renderSortButtons();
    _renderToggleChips();
    if (_state.loading) {
        document.title = _config ? _config.defaultTitle : "";
        _el.status.textContent = "データを読み込み中です。";
        _renderMessageRow("データを読み込み中です。");
        return;
    }
    if (_state.error) {
        document.title = _config ? _config.defaultTitle : "";
        _el.status.textContent = _state.error;
        _renderMessageRow(_state.error);
        return;
    }
    const visible = _getVisibleRows();
    const tabName = _getActiveTabName();
    document.title = tabName ? tabName + " - " + _config.defaultTitle : _config.defaultTitle;
    _el.status.textContent = visible.length.toLocaleString("ja-JP") + " 件";
    if (visible.length === 0) {
        _renderMessageRow("該当する銘柄はありません。");
        return;
    }
    _renderBody(visible);
}
function _renderHead() {
    if (!_el.thead) {
        return;
    }
    const tr = _el.thead.querySelector("tr") || document.createElement("tr");
    tr.innerHTML = "";
    for (const col of _config ? _config.columns : []) {
        const th = document.createElement("th");
        th.scope = "col";
        if (col.title) {
            th.title = col.title;
        }
        if (col.isPosition) {
            th.className = "column-position";
        }
        th.dataset.columnKey = col.key;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "sort-button";
        btn.dataset.sortColumn = col.key;
        btn.textContent = col.header + " ";
        const arrow = document.createElement("span");
        arrow.className = "arrow";
        arrow.setAttribute("aria-hidden", "true");
        arrow.textContent = INACTIVE_ARROW;
        btn.appendChild(arrow);
        th.appendChild(btn);
        tr.appendChild(th);
    }
    if (!_el.thead.contains(tr)) {
        _el.thead.appendChild(tr);
    }
}
function _renderBody(rows) {
    if (!_el.tbody || !_config) {
        return;
    }
    const cols = _config.columns;
    const hidden = _state.hiddenCols;
    const context = { githubPages: !!_config.githubPages };
    _el.tbody.innerHTML = rows.map(function (row) {
        const cells = cols.map(function (col) {
            const hiddenCls = hidden.has(col.key) ? " hidden-col" : "";
            const baseCls = col.cssClass || "";
            const extraCls = _metricCls(col, row);
            const cellClass = [col.type, baseCls, hiddenCls.trim(), extraCls.trim()].filter(Boolean).join(" ");
            return '<td class="' + escapeHtml(cellClass) + '">' + _renderCellContent(col, row, context) + "</td>";
        });
        return "<tr>" + cells.join("") + "</tr>";
    }).join("");
}
function _renderCellContent(col, row, context) {
    const content = col.render(row);
    if (!col.linkHref) {
        return content;
    }
    const href = col.linkHref(row, context);
    if (!href) {
        return content;
    }
    const attrs = [
        'href="' + escapeHtml(href) + '"',
        'target="_blank"',
        'rel="noopener"',
    ];
    const linkMode = _resolveLinkMode(col, row, context);
    if (linkMode === "browser" && col.browserKey) {
        attrs.push('data-browser="' + escapeHtml(col.browserKey) + '"');
    }
    else if (linkMode === "yazi") {
        attrs.push("data-yazi");
    }
    return "<a " + attrs.join(" ") + ">" + escapeHtml(content) + "</a>";
}
function _resolveLinkMode(col, row, context) {
    if (!col.linkMode) {
        return undefined;
    }
    return typeof col.linkMode === "function" ? col.linkMode(row, context) : col.linkMode;
}
function _renderMessageRow(message) {
    if (!_el.tbody || !_config) {
        return;
    }
    const visibleCount = _config.columns.length - _state.hiddenCols.size;
    _el.tbody.innerHTML = '<tr><td class="table-message" colspan="' + visibleCount + '">' + escapeHtml(message) + "</td></tr>";
}
function _renderTabs() {
    if (!_el.tabBar || !_config || !_config.tabMode || !_state.rows) {
        if (_el.tabBar) {
            _el.tabBar.innerHTML = "";
        }
        return;
    }
    const tabMap = {};
    for (const r of _state.rows) {
        if (r._tabKey && !tabMap[r._tabKey]) {
            tabMap[r._tabKey] = r._tabName || r._tabKey;
        }
    }
    const entries = Object.entries(tabMap);
    _el.tabBar.innerHTML = entries.map(function (pair) {
        const isActive = pair[0] === _state.currentTab;
        return '<button class="tab' + (isActive ? " active" : "") + '" type="button" data-tab-key="' + escapeHtml(pair[0]) + '" aria-selected="' + String(isActive) + '">' + escapeHtml(pair[1]) + "</button>";
    }).join("");
}
function _renderSortButtons() {
    if (!_el.thead) {
        return;
    }
    _el.thead.querySelectorAll(".sort-button").forEach(function (btn) {
        const key = btn.dataset.sortColumn;
        const isActive = _state.sortKey === key;
        const arrow = btn.querySelector(".arrow");
        if (arrow) {
            arrow.textContent = isActive ? (_state.sortDir === "asc" ? ASC_ARROW : DESC_ARROW) : INACTIVE_ARROW;
        }
        btn.classList.toggle("active", isActive);
        const th = btn.closest("th");
        if (th) {
            th.setAttribute("aria-sort", isActive ? (_state.sortDir === "asc" ? "ascending" : "descending") : "none");
        }
    });
}
function _renderToggleChips() {
    if (!_el.toggleBar || !_config) {
        return;
    }
    const toggleable = _config.columns.filter(function (c) { return !!c.toggleable; });
    _el.toggleBar.innerHTML = toggleable.map(function (col) {
        const isActive = !_state.hiddenCols.has(col.key);
        return '<button class="toggle-chip' + (isActive ? " active" : "") + '" type="button" data-toggle-column="' + escapeHtml(col.key) + '">' + escapeHtml(col.header) + "</button>";
    }).join("");
}
/* ------------------------------------------------------------------ */
/*  Metric colouring                                                   */
/* ------------------------------------------------------------------ */
function _metricCls(col, row) {
    if (!_config) {
        return "";
    }
    const t = _config.metricThresholds[col.key];
    if (!t) {
        return "";
    }
    const raw = col.sortValue ? col.sortValue(row) : null;
    if (raw === null || raw === undefined) {
        return "";
    }
    if (t.good && t.good(raw)) {
        return " metric-good";
    }
    if (t.bad && t.bad(raw)) {
        return " metric-bad";
    }
    return "";
}
/* ------------------------------------------------------------------ */
/*  Sorting & filtering                                                */
/* ------------------------------------------------------------------ */
function _getVisibleRows() {
    if (!_state.rows || !_config) {
        return [];
    }
    let rows = _state.rows;
    if (_config.tabMode && _state.currentTab) {
        rows = rows.filter(function (r) { return r._tabKey === _state.currentTab; });
    }
    return rows.slice().sort(function (a, b) {
        const col = _findColumn(_state.sortKey);
        if (!col || !col.sortValue) {
            return 0;
        }
        const av = col.sortValue(a);
        const bv = col.sortValue(b);
        const dir = _state.sortDir === "asc" ? 1 : -1;
        if (av === null && bv === null) {
            return (a.code || "").localeCompare(b.code || "", "ja", { numeric: true });
        }
        if (av === null) {
            return 1;
        }
        if (bv === null) {
            return -1;
        }
        return (av - bv) * dir;
    });
}
function _getActiveTabName() {
    if (!_state.rows || !_state.currentTab) {
        return "";
    }
    const row = _state.rows.find(function (r) { return r._tabKey === _state.currentTab; });
    return row ? row._tabName || "" : "";
}
function _findColumn(key) {
    if (!_config) {
        return null;
    }
    for (const col of _config.columns) {
        if (col.key === key) {
            return col;
        }
    }
    return null;
}
/* ------------------------------------------------------------------ */
/*  Event binding                                                      */
/* ------------------------------------------------------------------ */
function _bindEvents() {
    /* sort buttons */
    document.addEventListener("click", function (e) {
        const btn = e.target instanceof Element ? e.target.closest(".sort-button") : null;
        if (!btn) {
            return;
        }
        const key = btn.dataset.sortColumn;
        if (!key) {
            return;
        }
        if (_state.sortKey === key) {
            _state.sortDir = _state.sortDir === "asc" ? "desc" : "asc";
        }
        else {
            _state.sortKey = key;
            _state.sortDir = key === _config.defaultSortKey ? _config.defaultSortDirection : "asc";
        }
        _render();
    });
    /* toggle chips */
    if (_el.toggleBar) {
        _el.toggleBar.addEventListener("click", function (e) {
            const chip = e.target instanceof Element ? e.target.closest("[data-toggle-column]") : null;
            if (!chip) {
                return;
            }
            const col = chip.dataset.toggleColumn;
            if (!col) {
                return;
            }
            if (_state.hiddenCols.has(col)) {
                _state.hiddenCols.delete(col);
            }
            else {
                _state.hiddenCols.add(col);
            }
            _saveHiddenCols(_state.hiddenCols);
            _render();
        });
    }
    /* tabs */
    if (_el.tabBar) {
        _el.tabBar.addEventListener("click", function (e) {
            const tab = e.target instanceof Element ? e.target.closest("[data-tab-key]") : null;
            if (!tab) {
                return;
            }
            const key = tab.dataset.tabKey;
            if (!key) {
                return;
            }
            _state.currentTab = key;
            _state.sortKey = _config ? _config.defaultSortKey : "";
            _state.sortDir = _config ? _config.defaultSortDirection : "asc";
            _render();
        });
    }
    /* browser link interception */
    document.addEventListener("click", function (e) {
        const link = e.target instanceof Element ? e.target.closest("a[data-browser]") : null;
        if (!link) {
            return;
        }
        const browserKey = link.getAttribute("data-browser") || "";
        const url = link.href;
        e.preventDefault();
        fetch("/open?browser=" + encodeURIComponent(browserKey) + "&url=" + encodeURIComponent(url))
            .then(function (response) {
            if (!response.ok) {
                window.open(url, "_blank", "noopener");
            }
        })
            .catch(function () {
            window.open(url, "_blank", "noopener");
        });
    });
    /* yazi link interception */
    document.addEventListener("click", function (e) {
        const link = e.target instanceof Element ? e.target.closest("a[data-yazi]") : null;
        if (!link) {
            return;
        }
        e.preventDefault();
        fetch(link.href).catch(function () { });
    });
}
/* ------------------------------------------------------------------ */
/*  LocalStorage helpers                                               */
/* ------------------------------------------------------------------ */
function _loadHiddenCols() {
    try {
        const stored = localStorage.getItem(HIDDEN_COLUMNS_KEY);
        if (stored) {
            return new Set(JSON.parse(stored));
        }
    }
    catch (_e) {
        localStorage.removeItem(HIDDEN_COLUMNS_KEY);
    }
    return new Set();
}
function _saveHiddenCols(cols) {
    try {
        localStorage.setItem(HIDDEN_COLUMNS_KEY, JSON.stringify(Array.from(cols)));
    }
    catch (_e) {
        /* storage full or blocked → silently degrade */
    }
}
/* ------------------------------------------------------------------ */
/*  Utilities                                                          */
/* ------------------------------------------------------------------ */
function escapeHtml(value) {
    return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}
