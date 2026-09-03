// Answer-rendering eval. FREE, deterministic, no DB, no tokens, ~0.2s.
//
//     node tools_render_eval.js
//
// The other evals check whether the answer is TRUE. This one checks whether the
// reader can see it. They are different failures with the same cost: a correct
// answer rendered as a wall of literal pipes reads as a broken tool, and the
// user cannot tell which of the two went wrong.
//
// It runs the real renderer out of templates/base.html rather than a copy -
// the functions are eval'd from the file against a stub DOM, so this suite
// cannot drift away from what the browser executes. If base.html is
// restructured so the markers below move, extraction fails loudly instead of
// silently testing nothing.
//
// Every case here is a bug that shipped, or the boundary next to one.
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, 'templates', 'base.html');
const src = fs.readFileSync(FILE, 'utf8');
const FROM = '        var SEP_RE=';
const TO = '        function addMsg(role, text){';
const from = src.indexOf(FROM), to = src.indexOf(TO);
if (from < 0 || to < 0) {
  console.error('EXTRACT FAILED: templates/base.html no longer contains the\n' +
                'renderer between "' + FROM.trim() + '" and "' + TO.trim() + '".\n' +
                'The suite tested nothing. Fix the markers.');
  process.exit(2);
}

// --- the smallest DOM that renderAnswer needs ------------------------------
class El {
  constructor(tag) {
    this.tag = tag; this.className = ''; this.children = [];
    this._html = ''; this._text = '';
  }
  appendChild(c) { this.children.push(c); return c; }
  set innerHTML(v) { this._html = v; }
  get innerHTML() { return this._html; }
  set textContent(v) { this._text = v; }
  get textContent() { return this._text; }
  find(cls) {
    const out = [];
    const walk = e => {
      if (e.className && String(e.className).split(' ').indexOf(cls) >= 0) out.push(e);
      e.children.forEach(walk);
    };
    this.children.forEach(walk);
    return out;
  }
  tags(t) {
    const out = [];
    const walk = e => { if (e.tag === t) out.push(e); e.children.forEach(walk); };
    this.children.forEach(walk);
    return out;
  }
}
global.document = { createElement: t => new El(t) };
eval(src.slice(from, to));   // eslint-disable-line no-eval

let pass = 0;
const failures = [];
function check(group, name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; return; }
  failures.push({ group: group, name: name, got: g, want: w });
}
function render(text) { const b = new El('div'); renderAnswer(b, text); return b; }
function parse(text) {
  const r = pipeRun(text.split('\n'), 0);
  return r ? { head: r.head, rows: r.rows, end: r.end } : null;
}

// --- the parser ------------------------------------------------------------
// THE bug: a three-column product listing arrived with no |---|---| row and
// landed on screen as raw pipes. Markdown requires that row; models omit it.
let g = 'separator row is optional';
const shot = parse(
  '| Product | TCO ID | Status |\n' +
  '| Gas Analyzer 48i CO analyzer | IEC-EMC-002 | Assigned Lab Engineer |\n' +
  '| Smart2pure 6UV | IEC-EMC-004 | Test Plan Approved |');
check(g, 'detected without a separator', shot !== null, true);
check(g, 'first row is the header', shot && shot.head, ['Product', 'TCO ID', 'Status']);
check(g, 'both data rows kept', shot && shot.rows.length, 2);
check(g, 'cells intact', shot && shot.rows[0],
  ['Gas Analyzer 48i CO analyzer', 'IEC-EMC-002', 'Assigned Lab Engineer']);
check(g, 'run ends at the last row', shot && shot.end, 3);
const withSep = parse('| A | B |\n|---|---|\n| 1 | 2 |');
check(g, 'a separator still works', withSep && withSep.rows, [['1', '2']]);

g = 'outer pipes optional (valid GFM)';
const bare = parse('Product | TCO ID | Status\n' +
                   'Smart2pure 6UV | IEC-EMC-004 | Draft\n' +
                   'Genpure UV | IEC-EMC-001 | Draft');
check(g, 'detected', bare !== null, true);
check(g, 'header', bare && bare.head, ['Product', 'TCO ID', 'Status']);
check(g, 'rows', bare && bare.rows.length, 2);

// Turn 124 wrote "Table: differences by tco_id" and then bulleted its rows.
// listBlock() claimed them before pipeRun ever ran, so seven columns of EUT
// configuration reached the screen as literal pipes inside two <li>. The model
// had said the word "Table" and still not produced one.
// A single newline inside a paragraph is a SOFT WRAP and joins with a space; a
// blank line starts a new paragraph. Preserving every newline broke prose
// mid-sentence wherever the model happened to wrap, and - because the bold and
// italic patterns are deliberately single-line - silently stopped rendering any
// inline span that straddled the wrap.
g = 'soft wraps join, blank lines separate';
const wrapped = render(
  'The unit went from FAIL to PASS between two\n' +
  'revisions of the same datasheet.\n' +
  '\n' +
  'A second paragraph.');
const wrapParas = wrapped.find('labai-para');
check(g, 'two paragraphs, not four lines', wrapParas.length, 2);
check(g, 'the wrap became a space',
  wrapParas[0] && wrapParas[0].innerHTML,
  'The unit went from FAIL to PASS between two revisions of the same datasheet.');
check(g, 'the blank line started a new one',
  wrapParas[1] && wrapParas[1].innerHTML, 'A second paragraph.');

g = 'an inline span straddling a wrap still renders';
const straddle = render(
  'Round 1 was rejected - *"Equipment listed are not the ones\n' +
  'which were in the Request object"*.');
const straddleHtml = (straddle.find('labai-para')[0] || {}).innerHTML || '';
check(g, 'italic closed across the wrap', straddleHtml.indexOf('<em>') >= 0, true);
check(g, 'no stray asterisk left on screen',
  straddleHtml.indexOf('*') < 0, true);

const boldWrap = render('There are **10** test requests and **43**\nassigned tests.');
const boldHtml = (boldWrap.find('labai-para')[0] || {}).innerHTML || '';
check(g, 'bold survives either side of a wrap',
  (boldHtml.match(/<strong>/g) || []).length, 2);

g = 'rows the model bulleted by mistake';
const bulleted =
  '- IEC-EMC-005 | EFT | eut_configuration="Tabletop" | eut_class="Class B"\n' +
  '- IEC-EMC-006 | CE | eut_configuration="Tabletop" | eut_class="Class B"';
const bulletedRun = parse(bulleted);
check(g, 'recognised as a table', bulletedRun !== null, true);
check(g, 'list marker is not a cell', bulletedRun && bulletedRun.rows[0][0], 'IEC-EMC-005');
const bulletedDom = render(bulleted);
check(g, 'renders as a table, not a list', bulletedDom.tags('li').length, 0);
check(g, 'both rows survive rendering - neither promoted to a header',
  bulletedDom.tags('tbody').reduce((n, b) => n + b.children.length, 0), 2);

// An ordinary bullet holding one pipe is still a bullet. pipeRun refuses a
// two-column run with no separator and no outer pipes, and listBlock only
// steps aside when pipeRun says yes - so this must not become a table.
g = 'a bullet with a stray pipe stays a bullet';
const strayPipe = render('- Draft | then Peer Review\n- Approved | or Rejected');
check(g, 'still a list', strayPipe.tags('li').length, 2);
check(g, 'no table built', strayPipe.tags('table').length, 0);

// With no separator row, body[0] is a HEADING only if it reads like one. Both
// of turn 124's rows were data, and promoting the first deleted a row from the
// answer. Where every row spells its own field names, the names come from the
// keys and both rows are kept.
g = 'headerless key="value" rows keep every row';
const keyed = parse(
  'IEC-EMC-005 | EFT | eut_configuration="Tabletop" | eut_class="Class B"\n' +
  'IEC-EMC-006 | CE | eut_configuration="Tabletop" | eut_class="Class B"');
check(g, 'detected', keyed !== null, true);
check(g, 'headings taken from the keys', keyed && keyed.head,
  ['', '', 'eut_configuration', 'eut_class']);
check(g, 'no row promoted away', keyed && keyed.rows.length, 2);
check(g, 'quotes stripped from keyed cells', keyed && keyed.rows[0][2], 'Tabletop');
check(g, 'unkeyed column keeps its raw value', keyed && keyed.rows[1][0], 'IEC-EMC-006');

// A ragged row must never shift a value under a header that does not describe
// it. A mislabelled value is the failure this whole tool exists to avoid.
g = 'ragged rows';
const ragged = parse('| A | B | C |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |');
check(g, 'short row padded, table survives', ragged && ragged.rows[0], ['1', '', '']);
check(g, 'excess merged into the last column, not dropped',
  ragged && ragged.rows[1], ['1', '2', '3 | 4']);

g = 'escaping and sentinels';
const escaped = parse('| Limit | Note |\n|---|---|\n| 30 dB | Class A \\| Class B |');
check(g, 'escaped pipe stays in its cell', escaped && escaped.rows[0],
  ['30 dB', 'Class A | Class B']);
const dash = parse('| A | B |\n|---|---|\n| - | - |\n| 1 | 2 |');
check(g, '"-" is a no-value placeholder, not a separator',
  dash && dash.rows, [['-', '-'], ['1', '2']]);
const blanks = parse('| A | B |\n|---|---|\n|  |  |\n|  | 2 |');
check(g, 'all-blank row dropped, blank first column kept',
  blanks && blanks.rows, [['', '2']]);

g = 'not a table';
check(g, 'a header alone', parse('| Product | Status |'), null);
check(g, 'header + separator, no data', parse('| A | B |\n|---|---|'), null);
check(g, 'two prose sentences that happen to contain a pipe', parse(
  'The record moved Draft | Accepted once the reviewer signed it off in August.\n' +
  'Every move is written to datasheet_status_history | to_status for the revision.'), null);
check(g, 'a fixed-width separator has no pipes', parse(
  'Product   Status\n-------   ------\nSmart2pure  Draft'), null);

g = 'misc parsing';
const midSep = parse('| A | B |\n|---|---|\n| 1 | 2 |\n|---|---|\n| 3 | 4 |');
check(g, 'a repeated separator anywhere is dropped',
  midSep && midSep.rows, [['1', '2'], ['3', '4']]);
const align = parse('| A | B | C |\n|:---|---:|:---:|\n| 1 | 2 | 3 |');
check(g, 'alignment colons', align && align.rows, [['1', '2', '3']]);
const crlf = parse('| A | B |\r\n|---|---|\r\n| 1 | 2 |\r');
check(g, 'CRLF input', crlf && crlf.rows, [['1', '2']]);
const bold = parse('| **Product** | **Status** |\n|---|---|\n| Smart2pure | Draft |');
check(g, 'bold header cells survive to inline()',
  bold && bold.head, ['**Product**', '**Status**']);

// --- the rendered tree -----------------------------------------------------
g = 'renders as a table, not a paragraph';
const shotDom = render(
  'Current EMC programs/products, with their TCO IDs and current status:\n\n' +
  '| Product | TCO ID | Status |\n' +
  '| Gas Analyzer 48i CO analyzer | IEC-EMC-002 | Assigned Lab Engineer |\n' +
  '| Smart2pure 6UV | IEC-EMC-004 | Test Plan Approved |');
check(g, 'one table element', shotDom.tags('table').length, 1);
check(g, 'headers', shotDom.tags('th').map(e => e.innerHTML),
  ['Product', 'TCO ID', 'Status']);
check(g, 'two body rows', shotDom.tags('tbody')[0].children.length, 2);
check(g, 'lead-in kept as prose', shotDom.find('labai-para').length, 1);
check(g, 'no literal pipe left in the prose',
  shotDom.find('labai-para')[0].innerHTML.indexOf('|') >= 0, false);

g = 'block ordering';
const mixed = render(
  '## Smart2pure 6UV\nTwo revisions, both approved.\n\n' +
  '| Test | Result |\n|---|---|\n| CE | Pass |\n\n' +
  '- first point\n- second point\n');
check(g, 'heading, prose, table, list', mixed.children.map(c => c.tag + ':' + c.className),
  ['div:labai-h', 'div:labai-para', 'div:labai-tablewrap', 'ul:labai-list']);
const ol = render('1. gather\n2. verify\n3. answer');
check(g, 'numbered list keeps its order', ol.tags('li').map(e => e.innerHTML),
  ['gather', 'verify', 'answer']);
const two = render('| A | B |\n| 1 | 2 |\n| 3 | 4 |\n' +
                   'And the second:\n' +
                   '| C | D |\n| 5 | 6 |\n| 7 | 8 |');
check(g, 'two tables with prose between', two.children.map(c => c.className),
  ['labai-tablewrap', 'labai-para', 'labai-tablewrap']);

// Six columns cannot fit the panel. They arrived clipped on the left with
// every cell broken mid-word, so the renderer converts rather than hopes.
g = 'wide tables fall back to blocks';
const wide = render(
  '| Job | Product | Test | Status | Failure Reason | Notes |\n' +
  '|---|---|---|---|---|---|\n' +
  '| DEMO-JOB-311 | DEMO Vantage Water Purifier | CE | Rejected | ' +
  'CE_LIMIT_EXCEEDED | Class A limit line applied |');
check(g, 'no table element', wide.tags('table').length, 0);
check(g, 'one block', wide.find('labai-rec').length, 1);
check(g, 'titled by the identifier', wide.find('labai-rec-h')[0].innerHTML, 'DEMO-JOB-311');
check(g, 'nothing dropped', wide.find('labai-v').map(e => e.innerHTML),
  ['DEMO Vantage Water Purifier', 'CE', 'Rejected', 'CE_LIMIT_EXCEEDED',
   'Class A limit line applied']);
check(g, 'labels line up with their values', wide.find('labai-k').map(e => e.innerHTML),
  ['Product', 'Test', 'Status', 'Failure Reason', 'Notes']);
// Dedup by index, not by value: two columns can hold the same string, and
// matching on the value dropped the second one.
const dup = render('| Product | A | B | C | D | Note |\n|---|---|---|---|---|---|\n' +
                   '| Smart2pure | x | y | z | w | Smart2pure |');
check(g, 'a value repeating the title is kept', dup.find('labai-v').map(e => e.innerHTML),
  ['x', 'y', 'z', 'w', 'Smart2pure']);

// Two empty bullets arrived under a schedule table and rendered as stray
// dashes. An item with nothing in it is not an item.
g = 'empty list markers';
const trailing = render('Scope: 23 of 61 entries are in progress.\n\n- \n-');
check(g, 'no stray dash survives',
  trailing.find('labai-para').map(e => e.innerHTML).join('').indexOf('-') >= 0, false);
check(g, 'no empty list element', trailing.tags('ul').length + trailing.tags('ol').length, 0);
const realList = render('- first\n-\n- second');
check(g, 'a real list around an empty marker keeps both items',
  realList.tags('li').map(e => e.innerHTML), ['first', 'second']);
check(g, 'a real --- rule is still a rule',
  render('above\n\n---\n\nbelow').tags('hr').length, 1);

// A heading is for a reader. The model labels columns with the identifier it
// selected, and reason_code / what_it_means / products_affected arrived on
// screen verbatim. NULL under Job Number arrived the same way.
g = 'headings and blanks are for a reader';
const raw = render('| reason_code | what_it_means | products_affected |\n|---|---|---|\n| CE_LIMIT_EXCEEDED | Conducted emission above the limit | 4 |');
check(g, 'snake_case headings humanised', raw.tags('th').map(e => e.innerHTML),
  ['Reason code', 'What it means', 'Products affected']);
check(g, 'an UPPER_SNAKE value is NOT touched', raw.tags('td')[0].innerHTML,
  'CE_LIMIT_EXCEEDED');
const already = render('| Job Number | Test |\n|---|---|\n| TFS-EMC-2026-002 | CE |');
check(g, 'a heading already written for a person is left alone',
  already.tags('th').map(e => e.innerHTML), ['Job Number', 'Test']);
const nulls = render('| Job Number | Test |\n|---|---|\n| NULL | CE |\n| None | ESD |');
check(g, 'NULL and None render as a dash, not as data',
  nulls.tags('td').map(e => e.innerHTML), ['—', 'CE', '—', 'ESD']);

// cellText only ever ran on table cells, so "Product type: NULL" reached a
// bullet list untouched. A NULL mid-sentence is still ordinary prose.
const bulletNull = render('- Product type: NULL\n- Class type: None\n- Model: DEMO-50199315\n- Note: NULL values are excluded');
check(g, 'a bullet whose whole value is NULL is dashed',
  bulletNull.tags('li').map(e => e.innerHTML),
  ['Product type: —', 'Class type: —',
   'Model: DEMO-50199315', 'Note: NULL values are excluded']);

g = 'escaping and alignment';
const xss = render('| Product | Note |\n|---|---|\n| <img src=x onerror=alert(1)> | <b>hi</b> |');
const cells = xss.tags('td').map(e => e.innerHTML);
check(g, 'no raw tag survives a cell',
  cells.every(c => c.indexOf('<img') < 0 && c.indexOf('<b>') < 0), true);
check(g, 'escaped form', cells[0], '&lt;img src=x onerror=alert(1)&gt;');
const nums = render('| TCO | Count | Margin |\n|---|---|---|\n| IEC-EMC-002 | 12 | -3.4 |');
check(g, 'numbers right-aligned, identifiers not',
  nums.tags('td').map(e => e.className), ['', 'num', 'num']);
const fixed = render('product     status\n----------  ------\nSmart2pure  Draft');
check(g, 'the fixed-width path still renders', fixed.tags('table').length, 1);

// --- report ----------------------------------------------------------------
let last = null;
failures.forEach(f => {
  if (f.group !== last) { console.log('\n' + f.group); last = f.group; }
  console.log('  FAIL ' + f.name + '\n         got  ' + f.got + '\n         want ' + f.want);
});
console.log('\n  ' + pass + '/' + (pass + failures.length) + ' rendering checks passed');
if (failures.length) {
  console.log('  ' + failures.length + ' FAILED - an answer that is correct will not look it.');
}
process.exit(failures.length ? 1 : 0);
