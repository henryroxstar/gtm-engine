/*
 * The page's "these figures have aged" banner. Inlined verbatim into the page by
 * gtm_core.email_campaign_dashboard.banner.stale_banner and loaded UNCHANGED by
 * tests/contracts/test_dashboard_banner.py under node, so the code the test runs is the code
 * that ships.
 *
 * NO READER-FACING PROSE LIVES HERE (same rule as filter.js: §R14's lint reads *.py only).
 * Python renders the card hidden with its sentences already in it; this file writes ONE
 * number and unhides.
 *
 * FAILS CLOSED. If the figures instant or the limit cannot be read, the card shows its
 * "no usable date" sentence — it never stays quiet on a value it cannot judge.
 *
 * THE INSTANT. data-figures is the exact moment the figures were fetched, normalised to UTC by
 * Python (health.figures_instant, the parser every age on the page uses), e.g.
 * 2026-09-25T12:00:00Z. It is measured as that instant — NOT as its calendar date's midnight,
 * which made the browser disagree with --check-fresh by up to a day for every stamp that carries
 * a time. A bare date (an older page, a hand-built one) is still read as UTC midnight, as
 * health._parse_fetched reads it, and a numeric offset (+08:00) is honoured if one is ever
 * written. The verdict is the same strict `>` on FRACTIONAL days as health.figures_state's
 * `over_limit`; the count shown is whole days, rounded down, as `age_days` is. More than a day
 * ahead is "unknown", as in health.figures_age_days; up to a day ahead is never over the limit,
 * so it stays hidden.
 *
 * WHAT IT CANNOT PLACE FAILS TOWARD SHOWING (intended; pinned in test_dashboard_banner.py). A year
 * below 100 is read by Date.UTC as 19xx, a five-digit year or a day the month lacks does not match
 * STAMP or fails the calendar check, and a year past 9000 is more than a day ahead: the first is
 * a stamp so old it is over any limit either way, the rest are "unknown", and both show the card.
 * Python never writes such a stamp onto a banner (a stamp already over the limit at build gets the
 * static strip instead), so this only ever reads a hand-edited attribute — and for that, saying
 * "this may be old" is the safe answer, never silence.
 */

var DAY_MS = 86400000;

/* date, or date + time + (Z | +hh:mm), with optional fractional seconds; nothing looser. */
var STAMP = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2}))?$/;

/* The instant a stamp names, in epoch ms, or null when it is not a real date and time. */
function instantMs(raw) {
  if (typeof raw !== 'string') { return null; }
  var m = STAMP.exec(raw);
  if (!m) { return null; }
  var y = +m[1], mo = +m[2], d = +m[3];
  var h = 0, mi = 0, s = 0, frac = 0, offset = 0;
  if (m[4] !== undefined) {
    h = +m[4]; mi = +m[5]; s = +m[6];
    if (h > 23 || mi > 59 || s > 59) { return null; }
    if (m[7]) { frac = +((m[7] + '00').slice(0, 3)); }
    if (m[8] !== 'Z') {
      var oh = +m[8].slice(1, 3), om = +m[8].slice(4, 6);
      if (oh > 23 || om > 59) { return null; }
      offset = (oh * 60 + om) * 60000 * (m[8].charAt(0) === '-' ? -1 : 1);
    }
  }
  var t = Date.UTC(y, mo - 1, d, h, mi, s, frac);
  if (new Date(t).getUTCMonth() !== mo - 1 || new Date(t).getUTCDate() !== d) { return null; }
  return t - offset;
}

/* Fractional days since the figures instant, or null when it cannot be trusted. */
function figuresAgeDays(raw, nowMs) {
  var t = instantMs(raw);
  if (t === null) { return null; }
  var age = (nowMs - t) / DAY_MS;
  if (!(age >= -1)) { return null; }
  return age;
}

function limitDays(raw) {
  return (typeof raw === 'string' && /^\d+$/.test(raw)) ? +raw : null;
}

/* {show, age}: age is whole days, or null when the verdict is "cannot tell". */
function decide(figures, limit, nowMs) {
  var lim = limitDays(limit);
  var age = figuresAgeDays(figures, nowMs);
  if (lim === null || age === null) { return { show: true, age: null }; }
  return { show: age > lim, age: Math.max(0, Math.floor(age)) };
}

function mountBanner(doc, nowMs) {
  var el = doc.getElementById('stale-banner');
  if (!el) { return; }
  var r = decide(el.getAttribute('data-figures'), el.getAttribute('data-limit'), nowMs);
  if (!r.show) { return; }
  var known = el.querySelector('[data-known]');
  var unknown = el.querySelector('[data-unknown]');
  var slot = el.querySelector('[data-age]');
  if (r.age === null) {
    if (unknown) { unknown.hidden = false; }
  } else {
    if (slot) { slot.textContent = String(r.age); }
    if (known) { known.hidden = false; }
  }
  el.hidden = false;
}

if (typeof document !== 'undefined') {
  mountBanner(document, Date.now());
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { instantMs: instantMs, figuresAgeDays: figuresAgeDays, limitDays: limitDays, decide: decide };
}
