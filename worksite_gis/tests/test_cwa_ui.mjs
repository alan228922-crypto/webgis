import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const map = fs.readFileSync(new URL('../../home/static/home/js/map.js', import.meta.url), 'utf8').replaceAll('\r\n', '\n');
const radar = map.slice(map.indexOf("  let radarOverlay"), map.search(/  onSelector\('\[data-live="radar"\]\x27/));

function runRadar() {
  const events = {};
  let groupSize = 0;
  const data = { overlay: {url: 'https://example.invalid/radar.png', bounds: [[1, 1], [2, 2]]} };
  const overlay = {
    once(type, callback) { events[type] = callback; return this; },
    off(type) { delete events[type]; return this; },
    addTo() { groupSize++; return this; },
  };
  const context = {
    api: async () => data,
    currentSlug: () => 'synthetic',
    L: {imageOverlay: () => overlay},
    radarGroup: {removeLayer: () => groupSize--, clearLayers: () => { groupSize = 0; }},
    setTimeout,
    clearTimeout,
  };
  const load = vm.runInNewContext(radar + '\nloadRadarLayer;', context);
  return {load, events, data, layerCount: () => groupSize};
}

test('radar waits for image success before announcing data', async () => {
  const fixture = runRadar();
  let settled = false;
  const loading = fixture.load().finally(() => { settled = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(settled, false);
  fixture.events.load();
  assert.equal(await loading, fixture.data);
  assert.equal(fixture.layerCount(), 1);
});

test('radar rejects failed image and removes the invalid overlay', async () => {
  const fixture = runRadar();
  const loading = fixture.load();
  await new Promise(resolve => setImmediate(resolve));
  fixture.events.error();
  await assert.rejects(loading, /雷達影像/);
  assert.equal(fixture.layerCount(), 0);
});
