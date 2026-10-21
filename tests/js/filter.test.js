/*
 * The page's filter interpreter, exercised as the page ships it.
 *
 * WHY THIS EXISTS. Everything else about this dashboard is checked in Python: the tile
 * provenance contract re-executes each `src` claim, the refusal contract pins the sentences
 * a scope must not render. The filter broke that: once the headline figures are recomputed
 * in the browser, the code deciding what "23 have a verified address" means when a country
 * is selected is JavaScript, and no server-side test can see it. This file is that logic's
 * only reader.
 *
 * It requires ../../gtm_core/email_campaign_dashboard/filter.js directly — the same bytes
 * `filters.script_block` inlines into the page — so there is no copy to drift.
 *
 * The fixture is shared with tests/contracts/test_dashboard_filter_payload.py, which holds
 * an independent Python implementation to the SAME expectations. Two implementations of one
 * rule is normally the shape this repo refuses; here it is the point, because agreement
 * between them is the only evidence that the number a reader sees after clicking is the
 * number the model computed.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const f = require('../../gtm_core/email_campaign_dashboard/filter.js');
const FIX = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixture.json'), 'utf8')
);

test('every fixture case: rows shown, accounts counted, groups tallied', () => {
  for (const c of FIX.cases) {
    const shown = f.selectRows(FIX.rows, c.filters);
    assert.strictEqual(shown.length, c.rows_shown, `${c.name}: rows shown`);
    assert.deepStrictEqual(f.groupCounts(shown), c.groups, `${c.name}: groups`);
    for (const [pred, want] of Object.entries(c.counts)) {
      assert.strictEqual(
        f.countAccounts(shown, pred), want, `${c.name}: ${pred}`
      );
    }
  }
});

test('an account worked by two campaigns counts once', () => {
  // ci 0 appears on rows 0 and 3. Four rows, three accounts — the whole reason the payload
  // carries an opaque company index instead of letting the browser count rows.
  assert.strictEqual(FIX.rows.length, 4);
  assert.strictEqual(f.countAccounts(FIX.rows, 'all'), 3);
});

test('the complementary predicates partition the accounts, at every selection', () => {
  // This is what makes the two subtraction sub-lines safe to recompute: they are counts of
  // disjoint sets that sum to the whole, so no selection can drive one negative.
  for (const c of FIX.cases) {
    const shown = f.selectRows(FIX.rows, c.filters);
    const all = f.countAccounts(shown, 'all');
    assert.strictEqual(
      f.countAccounts(shown, 'co_has_email') + f.countAccounts(shown, 'co_no_email'),
      all, `${c.name}: has_email | no_email must partition`
    );
    assert.ok(
      f.countAccounts(shown, 'co_role_inbox') <= f.countAccounts(shown, 'co_has_email'),
      `${c.name}: role inboxes cannot exceed the accounts with an address`
    );
  }
});

test('SEEDED POSITIVE CONTROL: a corrupted row makes the fixture fail', () => {
  // A harness that never actually reached the JS reads exactly like one that agreed with
  // it. So corrupt one row's boolean and require the expectations to stop holding — if
  // this passes, every assertion above is worth nothing.
  //
  // Row 1 (ci 1) is corrupted, not row 0: ci 0 is the SHARED account, and the property
  // asserted just below is precisely that corrupting one of its two rows changes nothing.
  // Seeding there would have produced a control that cannot convict, which is how a
  // positive control quietly becomes decoration.
  const rows = JSON.parse(JSON.stringify(FIX.rows));
  rows[1].co_has_email = false;
  assert.notStrictEqual(
    f.countAccounts(rows, 'co_has_email'), FIX.cases[0].counts.co_has_email,
    'corrupting a row did not change the count — the test is not reading the rows'
  );
});

test('an account keeps its facts while ANY of its rows carries them', () => {
  // The consequence of counting accounts rather than rows, stated as a property. "This
  // account has a verified address" is a fact about the account, so one of its two
  // campaign rows losing the flag does not revoke it — which is also why a selection that
  // hides one row does not drop the account out of the tile.
  const rows = JSON.parse(JSON.stringify(FIX.rows));
  rows[0].co_has_email = false;              // ci 0's alpha row
  assert.strictEqual(f.countAccounts(rows, 'co_has_email'), 2, 'ci 0 still has its beta row');
  rows[3].co_has_email = false;              // ci 0's beta row too
  assert.strictEqual(f.countAccounts(rows, 'co_has_email'), 1, 'now the account has none');
});

test('an unknown facet key matches nothing rather than everything', () => {
  // Fail closed. A typo in a `data-key` must empty the table, which a reader notices,
  // rather than silently ignore the selection, which reads as "no accounts were excluded".
  assert.strictEqual(f.selectRows(FIX.rows, [['not_a_key', 'x']]).length, 0);
});

test('a disabled select contributes no filter', () => {
  // `disabled` does not empty `.value` — it only withholds a field from form submission.
  // A single-value facet used to render as `<select disabled>` carrying its one option, so
  // the page decided a filter was active before the reader touched anything and greyed out
  // every unfilterable tile. Single-value facets are now plain text; this guards the shape.
  const sel = (attrs) => ({
    disabled: !!attrs.disabled,
    value: attrs.value,
    getAttribute: (n) => attrs[n],
  });
  const chosen = (sels) => {
    const out = [];
    sels.forEach((s) => {
      if (s.disabled) { return; }
      if (s.value) { out.push([s.getAttribute('data-key'), s.value]); }
    });
    return out;
  };
  assert.deepStrictEqual(
    chosen([sel({ disabled: true, value: 'Widget Suite', 'data-key': 'product' })]), [],
    'a disabled control registered as an active filter'
  );
  assert.deepStrictEqual(
    chosen([sel({ value: '', 'data-key': 'country_key' })]), [],
    'an unset select must contribute nothing'
  );
  assert.deepStrictEqual(
    chosen([sel({ value: 'singapore', 'data-key': 'country_key' })]),
    [['country_key', 'singapore']]
  );
});
