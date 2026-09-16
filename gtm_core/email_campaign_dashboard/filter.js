/*
 * The dashboard's client-side filter. Inlined verbatim into the page by
 * gtm_core.email_campaign_dashboard.filters.script_block, and loaded UNCHANGED by
 * tests/js/filter.test.js — one source, so the code the contract test exercises is the
 * code that ships. Extracting it from the HTML with a regex instead would let the two
 * drift apart in exactly the place no server-side test can see.
 *
 * NO READER-FACING PROSE LIVES HERE. Every sentence — each tile's "why this did not move",
 * the stale markers, the tripwire's explanation — is rendered by Python and emitted
 * `hidden`; this file only unhides it. §R14's derived-prose lint walks `*.py` and nothing
 * else, so a sentence moved in here would leave that lint green on a claim it cannot read.
 *
 * Effects are limited to three: toggle `hidden`/a class, write a NUMBER into a slot, and —
 * only when the page disagrees with its own payload — write that disagreement into the
 * tripwire's detail slot.
 */

/* Does this row survive the current selection? `f` is [[key, value]]. */
function matchesRow(r, f) {
  for (var i = 0; i < f.length; i++) {
    if (r[f[i][0]] !== f[i][1]) { return false; }
  }
  return true;
}

/*
 * DISTINCT ACCOUNTS satisfying `pred`, never a row count. The roster folds on
 * (campaign, company), so an account two campaigns are both working is two rows of work
 * and still one account. `ci` is an opaque company index; the payload carries no names.
 *
 * Every predicate is a membership test, never a subtraction: the page's two difference
 * sub-lines ship as their own complementary predicates so a filtered count cannot go
 * negative the way `contact_verified - named_seat` can.
 */
function countAccounts(rows, pred) {
  var seen = {}, n = 0;
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    if (pred !== 'all' && !r[pred]) { continue; }
    if (seen[r.ci] !== 1) { seen[r.ci] = 1; n += 1; }
  }
  return n;
}

/* Rows per worklist group. Group membership depends on no facet, so only counts change. */
function groupCounts(rows) {
  var per = {};
  for (var i = 0; i < rows.length; i++) {
    per[rows[i].group] = (per[rows[i].group] || 0) + 1;
  }
  return per;
}

function selectRows(rows, f) {
  return rows.filter(function (r) { return matchesRow(r, f); });
}

function mountFilter(ROWS) {
  var bar = document.getElementById('filterbar');
  if (!bar || !ROWS.length) { return; }
  var sels = Array.prototype.slice.call(bar.querySelectorAll('select'));
  var clear = document.getElementById('filter-clear');

  function each(sel, fn) {
    Array.prototype.forEach.call(document.querySelectorAll(sel), fn);
  }
  function num(n) { return n.toLocaleString('en-US'); }
  function chosen() {
    var f = [];
    sels.forEach(function (s) {
      /* `disabled` does NOT empty `.value` — it only withholds the field from form
         submission. A disabled select therefore reports its option and would register as an
         active filter the reader never set. Single-value facets no longer render as selects
         at all (filters.bar_html), so this is belt-and-braces for anything that regresses. */
      if (s.disabled) { return; }
      if (s.value) { f.push([s.getAttribute('data-key'), s.value]); }
    });
    return f;
  }

  function apply() {
    var f = chosen();
    var on = f.length > 0;
    var shown = selectRows(ROWS, f);

    var visible = {};
    shown.forEach(function (r) { visible[r.i] = 1; });
    each('[data-row]', function (el) {
      el.hidden = on && visible[el.getAttribute('data-row')] !== 1;
    });

    each('[data-count-pred]', function (el) {
      el.textContent = num(countAccounts(shown, el.getAttribute('data-count-pred')));
    });

    var per = groupCounts(shown);
    each('[data-group-count]', function (el) {
      var k = el.getAttribute('data-group-count');
      el.textContent = num(k === '*' ? shown.length : (per[k] || 0));
    });
    each('[data-group]', function (el) {
      el.hidden = on && !per[el.getAttribute('data-group')];
    });

    /* A figure the filter cannot honestly move says so, rather than sitting there looking
       like it answered the question that was just asked. */
    each('[data-filter="off"], [data-stale-when-filtered]', function (el) {
      if (on) { el.classList.add('stale'); } else { el.classList.remove('stale'); }
      Array.prototype.forEach.call(el.querySelectorAll('.stat-why, .why'), function (w) {
        w.hidden = !on;
      });
    });

    if (clear) { clear.hidden = !on; }
  }

  /* TRIPWIRE. With nothing selected this script must reproduce every server-rendered
     figure exactly; if it does not, the payload and the render have diverged and every
     number the filter goes on to write is wrong. Run BEFORE the first apply(), while the
     slots still hold what Python put in them, and shown to the reader rather than logged —
     a console warning in an offline file nobody has open is not a signal. */
  var drift = [];
  each('[data-count-pred]', function (el) {
    var pred = el.getAttribute('data-count-pred');
    var page = el.textContent.replace(/[^0-9]/g, '');
    var mine = String(countAccounts(ROWS, pred));
    if (page !== mine) { drift.push(pred + ' ' + page + '≠' + mine); }
  });
  if (drift.length) {
    var tw = document.getElementById('filter-tripwire');
    if (tw) {
      var slot = tw.querySelector('[data-tripwire-detail]');
      if (slot) { slot.textContent = drift.join('; '); }
      tw.hidden = false;
    }
  }

  sels.forEach(function (s) { s.addEventListener('change', apply); });
  if (clear) {
    clear.addEventListener('click', function () {
      sels.forEach(function (s) { s.value = ''; });
      apply();
    });
  }
  apply();
}

/* In the page: GTM_ROWS is defined just above and a document exists, so mount.
   Under node: neither holds, and the pure functions are exported for the contract test. */
if (typeof document !== 'undefined' && typeof GTM_ROWS !== 'undefined') {
  mountFilter(GTM_ROWS);
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    matchesRow: matchesRow,
    countAccounts: countAccounts,
    groupCounts: groupCounts,
    selectRows: selectRows
  };
}
