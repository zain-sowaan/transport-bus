// GENERATED FILE - DO NOT EDIT BY HAND.
// Generated 2026-09-11 from rta.py, tamm.py
// by extension/tools/generate_extractors.py. Change the constants in those
// Python modules and re-run the generator; edits made here are lost and,
// worse, silently diverge from what the server-side fetcher reads.
//
// Keyed by the `client_reader` each fetcher declares, which is the same name
// the server returns from get_client_fetch_target - so the reader that runs
// is always the one that portal's fetcher named.
//
// Each value is the portal-reading arrow function exactly as the fetcher
// evaluates it, comments and all - so a fix to either side is one
// regeneration away from reaching the other.

globalThis.PORTAL_EXTRACTORS = {

	// from rta.py
	rta: {

		// EXTRACT_ROWS_JS
		extractRows: () => [...document.querySelectorAll('div.finesRowList')].map(list => {
  const row = {};
  const cells = [...list.children];

  // First cell, no label: "MAKE MODEL, YEAR, COLOUR". Kept whole rather than
  // split - the make can itself contain a comma, and nothing downstream needs
  // the pieces.
  if (cells.length) row.vehicle = (cells[0].innerText || '').trim();

  cells.slice(1).forEach(cell => {
    const label = cell.querySelector('span');
    if (!label) return;
    const key = (label.textContent || '').trim().replace(/:$/, '');
    // The value is everything the label is not. Subtracting the label's own
    // text is what keeps "Amount" out of "Amount AED 200".
    const value = (cell.innerText || '').replace(label.textContent || '', '').trim();
    if (key) row[key] = value;
  });

  // RTA's internal row key, on the selection checkbox. Recorded because it is
  // the only identifier present before a detail panel is opened, which makes it
  // the one way to tell "this row was read" from "this row was skipped".
  const tr = list.closest('tr');
  const box = tr && tr.querySelector('input.p-checkbox-input');
  const aria = (box && box.getAttribute('aria-label')) || '';
  const key = aria.match(/\b(C?TCK)_(\d+)\b/);
  if (key) { row._rowKind = key[1]; row._rowId = key[2]; }

  return row;
}).filter(row => Object.keys(row).length > 1),

		// NEXT_PAGE_JS
		nextPage: () => {
  const next = document.querySelector('.p-paginator-next');
  if (!next) return null;
  if (next.disabled || next.classList.contains('p-disabled')) return null;
  next.click();
  return true;
},

		// READ_PANEL_JS
		readPanel: () => {
  const list = document.querySelector('div.dataList');
  if (!list) return null;
  const out = {};

  [...list.children].forEach(cell => {
    if (cell.tagName !== 'DIV') return;
    const label = cell.querySelector('span');
    if (!label) return;
    const key = (label.textContent || '').trim().replace(/:$/, '');
    if (!key) return;
    const value = cell.querySelector('p');
    if (value) { out[key] = (value.textContent || '').trim(); return; }
    // No <p>: the value is the <ul> that follows this div.
    const after = cell.nextElementSibling;
    if (after && after.tagName === 'UL') {
      out[key] = [...after.querySelectorAll('li')]
        .map(li => (li.innerText || '').trim()).filter(Boolean).join('; ');
    }
  });

  // The plate is structured markup, so the code and the number come out as
  // separate values instead of a string that has to be split back apart.
  const info = document.querySelector('div.vInfo');
  if (info) {
    const heading = info.querySelector('h4');
    if (heading) out._vehicle = (heading.textContent || '').trim();
    const plate = info.querySelector('[data-testid="PlateComponent"]');
    if (plate) {
      const code = plate.querySelector('[data-testid="PlateCharacter"]');
      out._code = code ? (code.textContent || '').trim() : null;
      const number = [...plate.children].find(child => child !== code);
      out._number = number ? (number.textContent || '').trim() : null;
    }
  }

  return out;
},
	},

	// from tamm.py
	tamm: {

		// EXTRACT_ROWS_JS
		extractRows: () => [...document.querySelectorAll('tr.ui-lib-table-row')]
  // The header carries the same row class as the data rows and differs only by
  // this flag. Without it the header parses into a fine whose ticket number is
  // the literal text "Fine Number" - which then stages as a real liability.
  .filter(tr => !tr.querySelector('td[data-is-header="true"]'))
  .map(tr => {
  const row = {};
  tr.querySelectorAll('td[data-id]').forEach(td => {
    row[td.getAttribute('data-id')] = (td.innerText || '').trim();
  });
  const plate = tr.querySelector('.ui-lib-number-plate');
  if (plate) {
    const text = sel => {
      const el = plate.querySelector(sel);
      return el ? (el.innerText || '').trim() : null;
    };
    row._code = text('.ui-lib-number-plate__serial-text-item');
    row._emirate = text('.ui-lib-number-plate__serial-area-item-ar');
    row._number = text('.ui-lib-number-plate__register-text');
  }
  return row;
}).filter(row => Object.keys(row).length > 1),

		// NEXT_PAGE_JS
		nextPage: () => {
  const root = document.querySelector('.ui-lib-pagination') || document;
  const leaves = [...root.querySelectorAll('*')].filter(el =>
    el.children.length === 0 && /^\d{1,3}$/.test((el.textContent || '').trim()));

  const isActive = el => {
    for (let n = el; n && n !== root; n = n.parentElement) {
      const cls = (n.getAttribute && (n.getAttribute('class') || '')) || '';
      if (/active|selected|current/i.test(cls)) return true;
      if (n.getAttribute && n.getAttribute('aria-current')) return true;
    }
    return false;
  };

  const numbered = leaves.map(el => ({el, n: parseInt((el.textContent || '').trim(), 10)}));
  const active = numbered.find(x => isActive(x.el));
  if (active) {
    const target = numbered.find(x => x.n === active.n + 1);
    if (target) {
      (target.el.closest('button, a, li') || target.el).click();
      return active.n + 1;
    }
  }

  const next = [...root.querySelectorAll('button, a')].find(b => {
    const label = ((b.getAttribute('aria-label') || '') + ' ' + (b.className || '')).toLowerCase();
    return /next/.test(label) && !b.hasAttribute('disabled');
  });
  if (next) { next.click(); return 'next'; }
  return null;
},

		// READ_PANEL_JS
		readPanel: () => {
  const labels = ['Ticket Number', 'Plate Number', 'Description',
                  'Fine Location', 'Status', 'Ticket Type'];
  const panel = [...document.querySelectorAll('div, section, aside')]
    .filter(el => {
      const t = el.innerText || '';
      return t.includes('Ticket Number') && t.includes('Description');
    })
    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
  if (!panel) return null;
  const lines = (panel.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const out = {};
  for (let i = 0; i < lines.length - 1; i++) {
    if (labels.includes(lines[i]) && !labels.includes(lines[i + 1])) {
      out[lines[i]] = lines[i + 1];
    }
  }
  return out;
},
	},
};

globalThis.PORTAL_ORIGINS = {
 "rta": "https://ums.rta.ae",
 "tamm": "https://www.tamm.abudhabi"
};
