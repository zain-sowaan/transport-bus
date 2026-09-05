// GENERATED FILE - DO NOT EDIT BY HAND.
// Generated 2026-09-05 from tamm.py
// by extension/tools/generate_extractors.py. Change the constants in that
// Python module and re-run the generator; edits made here are lost and,
// worse, silently diverge from what the server-side fetcher reads.
//
// Each value is the portal-reading arrow function exactly as the fetcher
// evaluates it, comments and all - so a fix to either side is one
// regeneration away from reaching the other.

globalThis.TAMM_EXTRACTORS = {

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
};
