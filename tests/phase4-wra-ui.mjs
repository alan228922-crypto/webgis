import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const text = fs.readFileSync(new URL('../home/static/home/js/map.js', import.meta.url), 'utf8');
const rasterCode = text.slice(text.indexOf('  let dynamicFloodOverlay'), text.indexOf('  function trafficColor'));
const status = {textContent: '待命'};
const overlays = [];
const group = new Set();
const requests = [];
let slug = 'a';

const request = url => new Promise(resolve => requests.push({url, resolve}));
const context = vm.createContext({
  $: () => status,
  onSelector: () => {},
  currentSlug: () => slug,
  dynamicFloodGroup: {removeLayer: layer => group.delete(layer), clearLayers: () => group.clear()},
  fetch: request,
  api: async url => (await request(url)).json(),
  L: {
    imageOverlay(url) {
      const handlers = {};
      const layer = {
        url,
        once(event, callback) { handlers[event] = callback; return this; },
        addTo() { group.add(this); return this; },
        fire(event) { handlers[event]?.(); },
      };
      overlays.push(layer);
      return layer;
    },
  },
});
vm.runInContext(rasterCode + '\nglobalThis.actions = {loadDynamicFloodLayer, stopDynamicFloodLayer};', context);
const {loadDynamicFloodLayer, stopDynamicFloodLayer} = context.actions;
const raster = {
  status: 'ok', forecast_hours: 2, region: 'taoyuan', source_time: '2025-02-01T01:00:00Z',
  overlay: {bounds: [[24.8, 120.9], [25.1, 121.3]], image_url: '/verified-image'},
};
function answer(request, payload = raster) {
  request.resolve({json: async () => payload});
}

const pending = loadDynamicFloodLayer();
assert.match(requests[0].url, /\/projects\/a\//);
assert.equal(status.textContent, '載入中');
answer(requests[0]);
await pending;
assert.equal(status.textContent, '影像載入中');
overlays[0].fire('load');
assert.match(status.textContent, /已載入/);

const brokenImage = loadDynamicFloodLayer();
answer(requests[1]);
await brokenImage;
assert.equal(group.size, 1);
overlays[1].fire('error');
assert.equal(group.size, 0);
assert.match(status.textContent, /影像載入失敗/);

const oldProject = loadDynamicFloodLayer();
stopDynamicFloodLayer();
slug = 'b';
const newProject = loadDynamicFloodLayer();
assert.match(requests[3].url, /\/projects\/b\//);
answer(requests[3], {...raster, status: 'fallback', freshness: 'stale', forecast_hours: 0});
await newProject;
overlays[2].fire('load');
assert.match(status.textContent, /0 小時回退.*過期快取/);
answer(requests[2]);
await oldProject;
assert.equal(overlays.length, 3);
assert.equal(group.size, 1);

const unverified = loadDynamicFloodLayer();
answer(requests[4], {status: 'unverified', overlay: null});
await unverified;
assert.match(status.textContent, /無法核對/);
assert.equal(group.size, 0);
console.log('WRA UI verified: image events, fallback/stale, and delayed project response.');
