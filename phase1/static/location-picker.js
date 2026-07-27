/*
 * Hierarchical location picker (PRD 4.2a) — shared between the public
 * request form (static/request.html) and the LOC triage UI
 * (static/index.html), so there's one implementation instead of two.
 *
 * Reads its color/type tokens from each page's own :root CSS variables
 * (--pine, --canvas, --khaki-line, etc. — already defined identically in
 * both pages) rather than shipping its own palette, so it always matches
 * whichever page it's mounted in without extra config.
 *
 * Usage:
 *   const picker = LocationPicker.mount(containerEl, {
 *     treeUrl: '/locations/tree',        // or '/public/locations/tree'
 *     fetchOpts: {},                     // extra fetch() options, e.g. auth headers
 *     initial: {id: 12, name: 'Shower House NJ A1-1 E'} | null,
 *     onChange: (assetId) => { ... },    // called with null if cleared
 *     placeholder: 'Start typing your camp or area…',
 *   });
 *   picker.getValue()          -> asset id or null
 *   picker.setValue(id, name)  -> select a location programmatically
 *   picker.destroy()
 */
(function (global) {
  let styleInjected = false;
  function injectStyleOnce() {
    if (styleInjected) return;
    styleInjected = true;
    const style = document.createElement('style');
    style.textContent = `
.loc-picker{position:relative;background:#fff;border:1px solid var(--khaki-line);border-radius:8px;}
.loc-picker.open{border-color:var(--pine);}
.loc-picker .lp-input-row{display:flex;align-items:center;gap:8px;padding:0 10px;}
.loc-picker input.lp-search{flex:1;border:none;outline:none;background:transparent;padding:12px 0;font-size:16px;font-family:var(--font-body);color:var(--ink);}
.loc-picker input.lp-search::placeholder{color:var(--ink-soft);opacity:.7;}
.loc-picker .lp-clear{border:none;background:none;color:var(--ink-soft);cursor:pointer;font-size:12px;padding:4px 6px;display:none;}
.loc-picker .lp-clear.show{display:inline-block;}
.loc-picker .lp-chip{display:none;margin:10px;padding:9px 11px;background:var(--pine);color:#fff;border-radius:6px;font-size:13px;align-items:center;justify-content:space-between;gap:8px;}
.loc-picker .lp-chip.show{display:flex;}
.loc-picker .lp-chip .lp-path{color:var(--hivis-soft);font-family:var(--font-mono);font-size:10.5px;display:block;margin-top:2px;}
.loc-picker .lp-chip .lp-name{font-weight:600;}
.loc-picker .lp-chip button{border:none;background:rgba(255,255,255,.18);color:#fff;border-radius:5px;padding:4px 9px;font-size:11px;cursor:pointer;}
.loc-picker .lp-toggle-row{display:flex;justify-content:flex-end;padding:0 10px 8px;}
.loc-picker .lp-toggle-row.hidden{display:none;}
.loc-picker .lp-toggle{font-family:var(--font-mono);font-size:10.5px;color:var(--pine);background:none;border:1px solid var(--khaki-line);border-radius:5px;padding:4px 8px;cursor:pointer;}
.loc-picker .lp-toggle:hover{border-color:var(--pine);}
.loc-picker .lp-body{max-height:320px;overflow-y:auto;border-top:1px solid var(--khaki-line);}
.loc-picker .lp-body.hidden{display:none;}
.loc-picker .lp-result{display:flex;flex-direction:column;padding:9px 12px;cursor:pointer;border-bottom:1px solid #f0ede1;}
.loc-picker .lp-result:hover,.loc-picker .lp-result.active{background:#F1EAD9;}
.loc-picker .lp-result .lp-r-name{font-size:13.5px;font-weight:600;color:var(--ink);}
.loc-picker .lp-result .lp-r-crumb{font-family:var(--font-mono);font-size:10.5px;color:var(--ink-soft);margin-top:2px;}
.loc-picker .lp-result .lp-branch-tag{display:inline-block;background:var(--pine);color:#fff;font-family:var(--font-body);font-weight:600;border-radius:4px;padding:1px 6px;margin-right:6px;font-size:9.5px;letter-spacing:.03em;}
.loc-picker .lp-empty{padding:16px 12px;color:var(--ink-soft);font-size:12.5px;text-align:center;}
.loc-picker ul.lp-tree,.loc-picker ul.lp-tree ul{list-style:none;margin:0;padding-left:0;}
.loc-picker ul.lp-tree{padding:4px 4px 8px;}
.loc-picker ul.lp-tree ul{padding-left:16px;border-left:1px dashed var(--khaki-line);margin-left:8px;}
.loc-picker .lp-node-row{display:flex;align-items:center;gap:6px;padding:5px 6px;border-radius:5px;cursor:pointer;font-size:13px;}
.loc-picker .lp-node-row:hover{background:#F1EAD9;}
.loc-picker .lp-node-row .lp-twisty{width:12px;height:12px;flex:0 0 auto;display:flex;align-items:center;justify-content:center;color:var(--ink-soft);transition:transform .12s ease;font-size:9px;}
.loc-picker .lp-node-row.expanded .lp-twisty{transform:rotate(90deg);}
.loc-picker .lp-node-row .lp-twisty.leaf{visibility:hidden;}
.loc-picker .lp-node-row.branch-root .lp-label{font-weight:700;color:var(--pine);}
.loc-picker .lp-node-row .lp-code{font-family:var(--font-mono);font-size:10px;color:var(--ink-soft);margin-left:auto;padding-left:6px;white-space:nowrap;}
.loc-picker .lp-children{overflow:hidden;max-height:0;transition:max-height .15s ease;}
.loc-picker .lp-children.open{max-height:none;}
`;
    document.head.appendChild(style);
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function flatten(nodes, path, out) {
    for (const n of nodes) {
      const newPath = path.concat([n.name]);
      out.push({ node: n, path: newPath });
      if (n.children && n.children.length) flatten(n.children, newPath, out);
    }
    return out;
  }

  // Small in-memory cache per treeUrl so mounting several pickers on one
  // page (e.g. a filter + a drawer field) doesn't refetch the same tree.
  const treeCache = {};
  async function fetchTree(treeUrl, fetchOpts) {
    if (treeCache[treeUrl]) return treeCache[treeUrl];
    const res = await fetch(treeUrl, fetchOpts || {});
    if (!res.ok) throw new Error('failed to load location list');
    const tree = await res.json();
    treeCache[treeUrl] = tree;
    return tree;
  }

  function mount(container, opts) {
    injectStyleOnce();
    const {
      treeUrl,
      fetchOpts = {},
      initial = null,
      onChange = () => {},
      placeholder = 'Start typing a location…',
    } = opts;

    let TREE = [];
    let FLAT = [];
    let selected = initial ? { id: initial.id, name: initial.name } : null;
    let mode = 'search';
    let currentResults = [];

    container.innerHTML = `
      <div class="loc-picker" tabindex="-1">
        <div class="lp-chip">
          <div>
            <span class="lp-name"></span>
            <span class="lp-path"></span>
          </div>
          <button type="button" class="lp-change">Change</button>
        </div>
        <div class="lp-input-wrap">
          <div class="lp-input-row">
            <input type="text" class="lp-search" placeholder="${escapeHtml(placeholder)}" autocomplete="off">
            <button type="button" class="lp-clear">Clear</button>
          </div>
          <div class="lp-toggle-row">
            <button type="button" class="lp-toggle">Browse full tree instead</button>
          </div>
        </div>
        <div class="lp-body lp-results hidden"></div>
        <div class="lp-body lp-tree hidden"></div>
      </div>
    `;
    const root = container.querySelector('.loc-picker');
    const chip = root.querySelector('.lp-chip');
    const chipName = root.querySelector('.lp-name');
    const chipPath = root.querySelector('.lp-path');
    const changeBtn = root.querySelector('.lp-change');
    const inputWrap = root.querySelector('.lp-input-wrap');
    const searchEl = root.querySelector('.lp-search');
    const clearBtn = root.querySelector('.lp-clear');
    const toggleBtn = root.querySelector('.lp-toggle');
    const resultsEl = root.querySelector('.lp-results');
    const treeEl = root.querySelector('.lp-tree');

    function pathFor(assetId) {
      const f = FLAT.find((x) => x.node.id === assetId);
      return f ? f.path.join(' \u203a ') : '';
    }

    function showSelected() {
      if (!selected) {
        chip.classList.remove('show');
        inputWrap.style.display = '';
        return;
      }
      chipName.textContent = selected.name;
      chipPath.textContent = pathFor(selected.id) || selected.name;
      chip.classList.add('show');
      inputWrap.style.display = 'none';
      resultsEl.classList.add('hidden');
      treeEl.classList.add('hidden');
    }

    function select(assetId, name) {
      selected = { id: assetId, name };
      showSelected();
      onChange(assetId);
    }

    changeBtn.addEventListener('click', () => {
      selected = null;
      showSelected();
      onChange(null);
      searchEl.value = '';
      searchEl.focus();
    });

    function renderResults(query) {
      const q = query.trim().toLowerCase();
      if (!q) {
        resultsEl.classList.add('hidden');
        resultsEl.innerHTML = '';
        currentResults = [];
        return;
      }
      const matches = FLAT.filter((x) => x.node.name.toLowerCase().includes(q)).slice(0, 40);
      currentResults = matches;
      if (matches.length === 0) {
        resultsEl.innerHTML = `<div class="lp-empty">No locations match &ldquo;${escapeHtml(query)}&rdquo;</div>`;
      } else {
        resultsEl.innerHTML = matches
          .map((m, i) => {
            const crumb = m.path.slice(0, -1).join(' \u203a ');
            return `<div class="lp-result" data-idx="${i}">
              <span class="lp-r-name">${escapeHtml(m.node.name)}</span>
              <span class="lp-r-crumb"><span class="lp-branch-tag">${escapeHtml(m.node.branch_label)}</span>${crumb ? escapeHtml(crumb) : 'Top level'}</span>
            </div>`;
          })
          .join('');
      }
      resultsEl.classList.remove('hidden');
      treeEl.classList.add('hidden');
    }

    searchEl.addEventListener('input', (e) => {
      clearBtn.classList.toggle('show', !!e.target.value);
      renderResults(e.target.value);
    });
    resultsEl.addEventListener('click', (e) => {
      const item = e.target.closest('.lp-result');
      if (!item) return;
      const idx = parseInt(item.dataset.idx, 10);
      const m = currentResults[idx];
      select(m.node.id, m.node.name);
    });
    clearBtn.addEventListener('click', () => {
      searchEl.value = '';
      clearBtn.classList.remove('show');
      renderResults('');
      searchEl.focus();
    });

    function nodeHtml(n, isRoot) {
      const hasChildren = n.children && n.children.length > 0;
      const rowClass = 'lp-node-row' + (isRoot ? ' branch-root' : '');
      const twisty = hasChildren ? '&#9656;' : '';
      return (
        '<li>' +
        `<div class="${rowClass}" data-id="${n.id}">` +
        `<span class="lp-twisty${hasChildren ? '' : ' leaf'}">${twisty}</span>` +
        `<span class="lp-label">${escapeHtml(n.name)}</span>` +
        `<span class="lp-code">${escapeHtml(n.code || '')}</span>` +
        '</div>' +
        (hasChildren
          ? `<div class="lp-children">${buildTreeHtml(n.children, false)}</div>`
          : '') +
        '</li>'
      );
    }
    function buildTreeHtml(nodes, isRoot) {
      return '<ul class="lp-tree">' + nodes.map((n) => nodeHtml(n, isRoot)).join('') + '</ul>';
    }
    function renderTree() {
      treeEl.innerHTML = buildTreeHtml(TREE, true);
    }
    treeEl.addEventListener('click', (e) => {
      const row = e.target.closest('.lp-node-row');
      if (!row) return;
      const li = row.parentElement;
      const childWrap = li.querySelector(':scope > .lp-children');
      if (childWrap) {
        const isOpen = childWrap.classList.toggle('open');
        row.classList.toggle('expanded', isOpen);
      } else {
        const id = parseInt(row.dataset.id, 10);
        const f = FLAT.find((x) => x.node.id === id);
        if (f) select(f.node.id, f.node.name);
      }
    });

    toggleBtn.addEventListener('click', () => {
      if (mode === 'search') {
        mode = 'tree';
        toggleBtn.textContent = 'Back to search';
        searchEl.style.display = 'none';
        clearBtn.classList.remove('show');
        if (!treeEl.innerHTML) renderTree();
        resultsEl.classList.add('hidden');
        treeEl.classList.remove('hidden');
      } else {
        mode = 'search';
        toggleBtn.textContent = 'Browse full tree instead';
        searchEl.style.display = '';
        treeEl.classList.add('hidden');
        searchEl.focus();
      }
    });

    searchEl.addEventListener('focus', () => root.classList.add('open'));
    const onDocClick = (e) => {
      if (!root.contains(e.target)) root.classList.remove('open');
    };
    document.addEventListener('click', onDocClick);

    // Load the tree, then reflect whatever `initial` was passed in.
    const ready = fetchTree(treeUrl, fetchOpts)
      .then((tree) => {
        TREE = tree;
        FLAT = flatten(tree, [], []);
        showSelected(); // now that FLAT is populated, path text can resolve
      })
      .catch(() => {
        resultsEl.classList.remove('hidden');
        resultsEl.innerHTML = '<div class="lp-empty">Could not load locations. Try reloading the page.</div>';
      });

    showSelected(); // show chip immediately with just the name, path fills in once loaded

    return {
      ready,
      getValue: () => (selected ? selected.id : null),
      setValue: (id, name) => {
        if (id == null) {
          selected = null;
        } else {
          selected = { id, name };
        }
        showSelected();
      },
      destroy: () => {
        document.removeEventListener('click', onDocClick);
        container.innerHTML = '';
      },
    };
  }

  global.LocationPicker = { mount };
})(window);
