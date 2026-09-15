const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const registry = new Map();
const context = vm.createContext({
  HTMLElement: class {},
  customElements: {get: (name) => registry.get(name), define: (name, value) => registry.set(name, value)},
  window: {kanvasInternals: {escapeHtml: String}},
});
vm.runInContext(fs.readFileSync('src/kasana/kanvas/static/kanvas-watch-orders.js', 'utf8'), context);
const {groupOrder, moveItems, episodeCode} = context.window.kanvasOrderInternals;
const clean = (value) => JSON.parse(JSON.stringify(value));
const editor = new (registry.get('kanvas-watch-order-workspace'))();
editor.setSequence = (ids) => { editor.ids = ids; return true; };
editor.status = () => {};
editor.addItems([1, 2]);
editor.addItems([3, 4]);
assert.deepEqual(clean(editor.ids), [1, 2, 3, 4]);
assert.equal(editor.selected.size, 0);
editor.selected.add(3);
editor.addItems([5]);
editor.addItems([6]);
assert.deepEqual(clean(editor.ids), [1, 2, 5, 6, 3, 4]);
assert.deepEqual([...editor.selected], [3]);
const episode = (id, parentId, number, extra = {}) => ({id, title: `Episode ${number}`, kind: 'episode', parentId, seasonNumber: 1, episodeNumber: number, ...extra});
const items = new Map([
  episode(1, 10, 1), episode(2, 10, 2, {episodeEndNumber: 3}), episode(3, 10, 4),
  episode(4, 20, 1), episode(5, 20, 2),
  {id: 6, kind: 'movie', title: 'Film'}, episode(7, 10, 6),
].map((item) => [item.id, item]));
const groups = (ids) => clean(groupOrder(ids, items)).map((group) => group.ids);
assert.deepEqual(groups([1, 2, 3, 4, 5, 6, 7]), [[1, 2, 3], [4, 5], [6], [7]]);
assert.deepEqual(groups([1, 6, 2, 3]), [[1], [6], [2, 3]]);
assert.deepEqual(groups([3, 2, 1]), [[3], [2], [1]]);
assert.equal(episodeCode(items.get(2)), 'S01 E02–03');
assert.equal(episodeCode({...items.get(2), episodeEndSeasonNumber: 2}), 'S01 E02–S02 E03');
assert.deepEqual(clean(moveItems([1, 2, 3, 4, 5, 6], new Set([1, 2, 3]), 6)), [4, 5, 1, 2, 3, 6]);
assert.deepEqual(clean(moveItems([1, 2, 3, 4, 5, 6], new Set([4, 5]), 1)), [4, 5, 1, 2, 3, 6]);
assert.deepEqual(clean(moveItems([1, 2, 3, 4, 5, 6], new Set([2, 4]), null)), [1, 3, 5, 6, 2, 4]);
assert.deepEqual(clean(moveItems([1, 2, 3], new Set([1, 2]), 2)), [1, 2, 3]);
assert.throws(() => moveItems([1, 2, 3], new Set([1]), 99), /destination/);
// A 1,000-episode franchise is complete after grouping and range movement.
const longItems = new Map(Array.from({length: 1000}, (_, i) => [i + 1, episode(i + 1, Math.floor(i / 20), i % 20 + 1)]));
const longIds = [...longItems.keys()];
assert.deepEqual(clean(groupOrder(longIds, longItems)).flatMap((group) => group.ids), longIds);
const moved = clean(moveItems(longIds, new Set(longIds.slice(100, 120)), 1));
assert.deepEqual(moved.slice(0, 20), longIds.slice(100, 120));
assert.equal(new Set(moved).size, 1000);
assert.deepEqual(clean(groupOrder(moved, longItems)).flatMap((group) => group.ids), moved);
console.log('browser watch-order checks passed');
