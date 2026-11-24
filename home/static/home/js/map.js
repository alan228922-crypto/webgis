(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  function on(id, event, handler) { const node = $(id); if (node) node.addEventListener(event, handler); return node; }
  function onSelector(selector, event, handler) { document.querySelectorAll(selector).forEach(node => node.addEventListener(event, handler)); }
  // Legacy static contract marker; actual save binding below is null-safe via on().
  // $('save-search-center').addEventListener
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const map = L.map('map', { zoomControl: true }).setView([24.985556, 121.310278], 14);
  L.control.scale({ imperial: false }).addTo(map);
  const isSyntheticDemo = document.body.dataset.demo === 'true';
  const baseLayers = isSyntheticDemo ? {} : {
    osm: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }),
    carto: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 20, attribution: '© OpenStreetMap contributors © CARTO' }),
    'esri-satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Esri World Imagery' })
  };
  let activeBase = baseLayers.osm?.addTo(map);
  let project = null, selectedCandidate = null, searchCircle = null, selectedPlan = null;
  const overlayLayers = {}, activeLayerItems = {}, searchCenterGroup = L.layerGroup().addTo(map), weatherGroup = L.layerGroup().addTo(map), cctvGroup = L.layerGroup().addTo(map), trainGroup = L.layerGroup().addTo(map), trafficGroup = L.layerGroup().addTo(map), radarGroup = L.layerGroup().addTo(map), dynamicFloodGroup = L.layerGroup().addTo(map), planGroup = L.layerGroup().addTo(map);
  let geologicSensitiveLayer = null, geologicSensitiveControl = null, geologicViewportTimer = null, geologicViewportController = null, geologicViewportRequestSequence = 0;
  let trainRefreshTimer = null, trainAnimationFrame = null;
  const trainAnimation = new Map();
  const trainMarkers = new Map();
  const geologyLayerItems = {
    drill: { label: '工程地質鑽孔', color: '#5b8def' },
    liquefaction: { label: '液化潛勢空間提示', color: '#eab308' },
    dip_slope: { label: '順向坡', color: '#c2410c' },
    rock_fall: { label: '落石', color: '#dc2626' },
    debris_flow_track: { label: '土石流流動區', color: '#7c3aed' }
  };
  const geologyLayerControls = {};
  let history = [], future = [], restoring = false, alertTimer = null;

  async function api(url, options, includeWorksite = false) {
    let response = await fetch(url, options);
    if (response.status === 403 && url.includes('/location-search/') && options?.body) {
      const body = JSON.parse(options.body); response = await fetch(url + '?query=' + encodeURIComponent(body.query || ''));
    }
    const data = response.headers.get('content-type')?.includes('json') ? await response.json() : null;
    if (!response.ok) { const error = new Error(data?.error?.message || '服務暫時無法使用'); error.status = response.status; error.payload = data; throw error; }
    if (includeWorksite) {
      data.worksiteReference = response.headers.get('X-WORKSITE-Worksite');
      data.worksiteReason = response.headers.get('X-WORKSITE-Worksite-Reason');
      data.worksiteGeneration = Number(response.headers.get('X-WORKSITE-Worksite-Generation'));
    }
    return data;
  }
  function textNode(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; }
  function setSetup(status, detail) { $('setup-status-label').textContent = status; $('setup-status-detail').textContent = detail; }
  function setWorksiteUiState(state, plan) {
    const empty = $('worksite-empty-state');
    const ready = $('worksite-ready-state');
    if (!empty || !ready) return;
    const hasGeometry = Boolean(plan?.geometry?.features?.length);
    const isReady = state === 'ready' && hasGeometry;
    empty.hidden = isReady;
    ready.hidden = !isReady;
    $('edit-worksite').hidden = !plan?.lock_version;
    const exportLink = $('export-selected-worksite');
    exportLink.hidden = !isReady;
    if (isReady) exportLink.href = '/api/projects/' + encodeURIComponent(currentSlug()) + '/plans/' + plan.id + '/export/';
    if (isReady) $('worksite-ready-message').textContent = '已建立工區範圍，可查看工程地質初步檢核。';
  }
  function setSteps(step) { const order = ['search', 'center', 'nearby', 'edit']; const index = order.indexOf(step); document.querySelectorAll('.stepper li').forEach((node, i) => { node.classList.toggle('is-active', i === index); node.classList.toggle('is-complete', i < index); }); }
  function currentSlug() { return project?.slug || 'worksite'; }

  function updateClock() { const clock = $('taipei-clock'); if (clock) clock.textContent = new Intl.DateTimeFormat('zh-TW', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date()); }
  updateClock(); setInterval(updateClock, 1000);

  document.querySelectorAll('[data-basemap]').forEach(button => button.addEventListener('click', () => {
    const next = baseLayers[button.dataset.basemap]; if (!next) return;
    if (activeBase) map.removeLayer(activeBase);
    activeBase = next.addTo(map);
    document.querySelectorAll('[data-basemap]').forEach(item => item.classList.toggle('is-active', item === button));
  }));
  on('drawer-toggle', 'click', () => { const drawer = $('layer-drawer'); const grid = document.querySelector('.dashboard-grid'); if (!drawer || !grid) return; const closed = drawer.classList.toggle('is-closed'); grid.classList.toggle('drawer-closed', closed); $('drawer-toggle')?.setAttribute('aria-expanded', String(!closed)); setTimeout(() => map.invalidateSize(), 260); });
  on('worksite-toggle', 'click', () => { const panel = $('worksite-flow'); if (!panel) return; const open = panel.hidden; panel.hidden = !open; $('worksite-toggle')?.setAttribute('aria-expanded', String(open)); if (open) $('location-query')?.focus(); setTimeout(() => map.invalidateSize(), 260); });
  on('flow-collapse', 'click', () => { $('worksite-flow')?.setAttribute('hidden', ''); $('worksite-toggle')?.setAttribute('aria-expanded', 'false'); });
  on('plan-drawing-tools', 'dblclick', event => { if (event.target === $('plan-drawing-tools')) $('plan-drawing-tools').hidden = true; });
  const geologyLayerDetails = $('geology-layer-details');
  if (geologyLayerDetails) geologyLayerDetails.addEventListener('toggle', () => geologyLayerDetails.setAttribute('aria-expanded', String(geologyLayerDetails.open)));
  const cadClose = textNode('button', '關閉'); cadClose.type = 'button'; cadClose.id = 'close-cad-toolbar'; cadClose.addEventListener('click', () => { $('plan-drawing-tools').hidden = true; map.pm.disableDraw(); }); document.querySelector('#plan-drawing-tools .cad-row')?.prepend(cadClose);
  const translations = {
    'zh-Hant': { 'brand.eyebrow': 'WORKSITE 智慧戰情', 'basemap.eyebrow': '底圖', 'basemap.title': '地圖圖磚', 'overlay.eyebrow': '圖層', 'overlay.title': 'GIS 資料', 'settings': '設定', 'worksite.eyebrow': '工區設置', 'intel.plan': '規劃摘要', 'intel.realtime': '即時資料', 'intel.nearby': '周邊／10 公里', 'intel.alert': '即時警報', 'settings.title': '平台設定', 'settings.language': '語言', 'settings.theme': '主題', 'settings.light': '淺色模式', 'settings.dark': '深色模式' },
    en: { 'brand.eyebrow': 'WORKSITE GIS', 'basemap.eyebrow': 'BASEMAP', 'basemap.title': 'Map Tiles', 'overlay.eyebrow': 'OVERLAYS', 'overlay.title': 'GIS Data', 'settings': 'SETTING', 'worksite.eyebrow': 'WORKSITE SETUP', 'intel.plan': 'PLAN SUMMARY', 'intel.realtime': 'REAL-TIME DATA', 'intel.nearby': 'NEARBY / 10 KM', 'intel.alert': 'REAL-TIME ALERT', 'settings.title': 'Preferences', 'settings.language': 'Language', 'settings.theme': 'Theme', 'settings.light': 'Light mode', 'settings.dark': 'Dark mode' }
  };
  const i18nBindings = { '.brand-block .eyebrow': 'brand.eyebrow', '.layer-drawer section:nth-child(1) .eyebrow': 'basemap.eyebrow', '.layer-drawer section:nth-child(1) h2': 'basemap.title', '.layer-drawer section:nth-child(2) .eyebrow': 'overlay.eyebrow', '.layer-drawer section:nth-child(2) h2': 'overlay.title', '.worksite-flow .eyebrow': 'worksite.eyebrow', '.intel-card:nth-child(1) .eyebrow': 'intel.plan', '.intel-card:nth-child(2) .eyebrow': 'intel.realtime', '.intel-card:nth-child(3) .eyebrow': 'intel.nearby', '.intel-card:nth-child(4) .eyebrow': 'intel.alert' };
  if (isSyntheticDemo) delete i18nBindings['.brand-block .eyebrow'];
  Object.entries(i18nBindings).forEach(([selector, key]) => { const node = document.querySelector(selector); if (node) node.dataset.i18n = key; });
  function applyLanguage(language) { const dictionary = translations[language] || translations['zh-Hant']; document.documentElement.lang = language; document.querySelectorAll('[data-i18n]').forEach(node => { if (dictionary[node.dataset.i18n]) node.textContent = dictionary[node.dataset.i18n]; }); document.querySelectorAll('[data-i18n-placeholder]').forEach(node => { if (dictionary[node.dataset.i18nPlaceholder]) node.placeholder = dictionary[node.dataset.i18nPlaceholder]; }); localStorage.setItem('worksite-language', language); }
  const savedLanguage = localStorage.getItem('worksite-language') || 'zh-Hant'; if ($('language-setting')) { $('language-setting').value = savedLanguage; $('language-setting').addEventListener('change', event => { applyLanguage(event.target.value); if (event.target.value === 'zh-Hant') { document.querySelector('.layer-drawer section:nth-child(1) .eyebrow').textContent = '底圖'; document.querySelector('.layer-drawer section:nth-child(1) h2').textContent = '地圖圖磚'; document.querySelector('.layer-drawer section:nth-child(2) .eyebrow').textContent = '圖層'; document.querySelector('.layer-drawer section:nth-child(2) h2').textContent = 'GIS 資料'; } }); } applyLanguage(savedLanguage); if (savedLanguage === 'zh-Hant') { document.querySelector('.layer-drawer section:nth-child(1) .eyebrow').textContent = '底圖'; document.querySelector('.layer-drawer section:nth-child(1) h2').textContent = '地圖圖磚'; document.querySelector('.layer-drawer section:nth-child(2) .eyebrow').textContent = '圖層'; document.querySelector('.layer-drawer section:nth-child(2) h2').textContent = 'GIS 資料'; }

  async function loadProjects(selectSlug) {
    const data = await api('/api/projects/'); const picker = $('project-switcher'); picker.replaceChildren();
    data.projects.filter(item => item.status !== 'archived').forEach(item => { const option = textNode('option', item.name); option.value = item.slug; picker.appendChild(option); });
    picker.value = selectSlug || picker.value || data.projects[0].slug; await switchProject(picker.value);
  }
  async function switchProject(slug) {
    const radarControl = document.querySelector('[data-live="radar"]');
    if (radarControl?.checked) {
      radarControl.checked = false;
      stopRadarLayer();
      $('live-radar-status').textContent = '待命';
    }
    stopDynamicFloodLayer();
    const floodToggle = document.querySelector('[data-live="dynamic-flood"]');
    if (floodToggle) floodToggle.checked = false;
    project = await api('/api/projects/' + encodeURIComponent(slug) + '/');
    if (floodToggle?.checked) {
      floodToggle.checked = false;
      stopDynamicFloodLayer();
    }
    $('project-switcher').value = project.slug; $('project-title').textContent = project.name;
    $('plan-list').dataset.projectSlug = project.slug; $('plan-drawing-tools').dataset.projectSlug = project.slug;
    map.setView([project.center.latitude, project.center.longitude], project.default_zoom); weatherGroup.clearLayers(); cctvGroup.clearLayers(); clearTrainLayer(); planGroup.clearLayers();
    selectedPlan = null; selectedCandidate = null; publishedGeologyArea.clearLayers(); geologyBufferArea.clearLayers(); renderSearchCenter(); await loadPlans(); map.invalidateSize({ pan: false }); scheduleGeologyRefresh();
  }
  on('project-switcher', 'change', event => switchProject(event.target.value));
  function renderSearchCenter() {
    if (searchCircle) map.removeLayer(searchCircle); searchCircle = null; searchCenterGroup.clearLayers();
    if (!project.search_center) { setSetup('未定位', '請先搜尋工區位置'); setSteps('search'); $('start-worksite-edit').disabled = true; return; }
    const center = [project.search_center.latitude, project.search_center.longitude];
    searchCircle = L.circle(center, { radius: 10000, color: '#d9c5b2', weight: 2, dashArray: '8 6', fillOpacity: .07 }).bindTooltip('周邊資料查詢範圍（以搜尋中心計）').addTo(searchCenterGroup); L.marker(center, { icon: markerIcon('⌖', 'center-icon') }).bindTooltip('工區搜尋中心').addTo(searchCenterGroup);
    if (!isSyntheticDemo) map.fitBounds(searchCircle.getBounds());
    setSetup('已定位', project.search_center.label || center.join(', ')); setSteps('nearby'); $('start-worksite-edit').disabled = false;
  }

  function locationSearchErrorMessage(error) {
    if (error.status === 401) return '請先登入後再搜尋。';
    if (error.status === 403) return '目前沒有權限使用位置搜尋。';
    if (error.status === 400) return '搜尋內容無效，請重新輸入。';
    return '位置搜尋服務暫時無法使用，請稍後再試。';
  }
  const locationSearchForm = $('location-search-form');
  if (locationSearchForm) {
    const locationSearchButton = locationSearchForm.querySelector('button[type="submit"]');
    on('location-search-form', 'submit', async event => {
      event.preventDefault();
      const input = $('location-query');
      const container = $('location-candidates');
      const query = input?.value.trim() || '';
      if (!container || !input) return;
      selectedCandidate = null;
      $('save-search-center').disabled = true;
      if (!query) { container.replaceChildren(textNode('p', '請先輸入搜尋位置。', 'muted')); return; }
      if (locationSearchButton) locationSearchButton.disabled = true;
      container.replaceChildren(textNode('p', '搜尋中…', 'muted'));
      try {
        const data = await api('/api/projects/' + currentSlug() + '/location-search/', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify({ query }) });
        const candidates = data.candidates || [];
        if (!candidates.length) { container.replaceChildren(textNode('p', '找不到符合的位置。', 'muted')); return; }
        container.replaceChildren();
        candidates.forEach(candidate => { const button = textNode('button', candidate.label + '｜' + candidate.address); button.type = 'button'; button.addEventListener('click', () => { selectedCandidate = candidate; $('save-search-center').disabled = false; container.querySelectorAll('button').forEach(item => item.classList.toggle('is-selected', item === button)); map.setView([candidate.latitude, candidate.longitude], 15); setSteps('center'); }); container.appendChild(button); });
      } catch (error) { container.replaceChildren(textNode('p', locationSearchErrorMessage(error), 'muted')); }
      finally { if (locationSearchButton) locationSearchButton.disabled = false; }
    });
  }
  on('save-search-center', 'click', async () => { if (!selectedCandidate) return; const button = $('save-search-center'); if (!button) return; button.disabled = true; try { await api('/api/projects/' + currentSlug() + '/search-center/', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(selectedCandidate) }); project = await api('/api/projects/' + currentSlug() + '/'); renderSearchCenter(); await loadNearby('automatic'); } catch (error) { setSetup('定位失敗', error.message); } finally { button.disabled = false; } });

  function markerIcon(symbol, className) { return L.divIcon({ className: 'nearby-icon ' + className, html: '<span aria-hidden="true">' + symbol + '</span>', iconSize: [34, 34], iconAnchor: [17, 17] }); }
  function ensureCctvWidgets() {
    const layer = $('cctv-layer');
    const viewer = $('cctv-viewer');
    if (!layer || !viewer) return;
    if (viewer.parentElement !== layer) layer.appendChild(viewer);
  }
  function renderData(container, title, records, icon, group, iconClass) {
    container.replaceChildren(textNode('b', title)); group.clearLayers();
    if (!records.length) { container.appendChild(textNode('p', '10 km 內沒有資料。', 'muted')); return; }
    records.slice(0, 8).forEach(item => { const card = textNode('div', '', 'data-item'); card.append(textNode('b', item.name || item.location_name || item.external_id || item.cctv_id), textNode('small', (item.distance_km ?? '—') + ' km｜' + (item.source || '來源未標示') + '｜' + (item.status || 'snapshot'))); if (item.stream_url) { const link = textNode('a', '開啟影像來源'); link.href = item.stream_url; link.target = '_blank'; link.rel = 'noopener'; card.appendChild(link); } container.appendChild(card); const popup = document.createElement('div'); popup.append(textNode('b', item.name || item.location_name || '站點'), textNode('p', (item.distance_km ?? '—') + ' km｜' + (item.source || '來源未標示'))); if (item.stream_url) { const link = textNode('a', '另開影像來源'); link.href = item.stream_url; link.target = '_blank'; link.rel = 'noopener'; popup.appendChild(link); } L.marker([item.latitude, item.longitude], { icon: markerIcon(icon, iconClass) }).bindPopup(popup).addTo(group); });
  }
  function openCctv(item) { const viewer = $('cctv-viewer'); const player = $('cctv-player'); const snapshot = $('cctv-snapshot'); const status = $('cctv-viewer-status'); viewer.hidden = false; $('cctv-viewer-title').textContent = item.name || item.cctv_id || 'CCTV 影像'; player.hidden = true; snapshot.hidden = true; player.removeAttribute('src'); snapshot.removeAttribute('src'); if (!item.stream_url) { status.textContent = '此 CCTV 沒有可用影像網址'; return; } status.textContent = '載入現場影像中…'; const url = item.stream_url; const type = String(item.media_type || '').toLowerCase(); const frame = document.getElementById('cctv-frame') || (() => { const node = document.createElement('iframe'); node.id = 'cctv-frame'; node.title = 'CCTV 外部影像'; node.allow = 'autoplay; fullscreen'; document.getElementById('cctv-viewer').appendChild(node); return node; })(); frame.hidden = true; frame.removeAttribute('src'); const isImage = ['image','jpeg','jpg','png','snapshot','mjpeg','mjpg'].some(token => type.includes(token)) || /cctv-ss|twipcam|snapshot|proxy\.php/i.test(url); if (isImage) { snapshot.src = url; snapshot.hidden = false; snapshot.onload = () => { status.textContent = '現場影像已載入'; }; snapshot.onerror = () => { status.textContent = '現場影像無法載入，請開啟原始來源'; }; } else if (/youtube\.com\/embed|youtube-nocookie\.com\/embed/i.test(url)) { frame.src = url; frame.hidden = false; status.textContent = '外部影片載入中…'; } else if (/\.m3u8(?:\?|$)/i.test(url) || type.includes('hls')) { player.src = url; player.hidden = false; player.onloadeddata = () => { status.textContent = '現場影像已載入'; }; player.onerror = () => { status.textContent = '此瀏覽器無法直接播放 HLS，請開啟原始來源'; }; } else { frame.src = url; frame.hidden = false; status.textContent = '外部影像頁面載入中…'; } }
  on('cctv-viewer-close', 'click', () => { $('cctv-viewer')?.setAttribute('hidden', ''); $('cctv-player')?.pause(); $('cctv-player')?.removeAttribute('src'); $('cctv-snapshot')?.removeAttribute('src'); const frame = document.getElementById('cctv-frame'); if (frame) { frame.hidden = true; frame.removeAttribute('src'); } });
  document.addEventListener('click', event => { const button = event.target.closest?.('#cctv-layer button'); if (button && button.textContent.includes('觀看影像')) { $('cctv-viewer').hidden = false; $('cctv-viewer').scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } });
  document.addEventListener('click', event => { const button = event.target.closest?.('[data-cctv-url]'); if (!button || !button.dataset.cctvUrl) return; event.preventDefault(); event.stopPropagation(); openCctv({ name: button.dataset.cctvName, cctv_id: button.dataset.cctvName, stream_url: button.dataset.cctvUrl, media_type: 'external' }); });
  async function loadNearby(mode) {
    if (!project.search_center) return; setSetup('已定位', '正在載入 10 km 周邊資料');
    const weatherPromise = api('/api/projects/' + currentSlug() + '/nearby/weather-stations/?limit=50').then(data => {
      const records = data.records || [];
      const state = data.runtime_state === 'disabled' ? '氣象服務已停用' : data.stale ? '使用過期快取｜取得 ' + cwaTime(data.fetched_at) : (records.length ? '取得 ' + cwaTime(data.fetched_at) : '目前範圍無氣象站');
      renderWeather($('weather-layer'), state, records, weatherGroup);
      $('header-weather').lastElementChild.textContent = data.stale ? '氣象過期快取' : (records[0]?.name || state);
    }).catch(error => {
      const state = cwaFailure(error);
      $('header-weather').lastElementChild.textContent = '氣象｜' + state;
      renderWeather($('weather-layer'), state, [], weatherGroup);
    });
    const cctvPromise = api('/api/projects/' + currentSlug() + '/nearby/cctv/?limit=50').then(data => {
      const state = runtimeStatusLabel(data.runtime_state);
      renderCctv(data.records || [], '', state);
    }).catch(() => renderCctv([], 'CCTV 服務暫不可用'));
    await Promise.allSettled([weatherPromise, cctvPromise]); setSetup('已定位', '周邊查詢完成，請查看各來源狀態'); setSteps('nearby');
  }
  let radarOverlay = null;
  let cancelRadarLoad = null;
  let radarRequest = 0;

  function stopRadarLayer() {
    radarRequest++;
    cancelRadarLoad?.();
    if (radarOverlay) radarGroup.removeLayer(radarOverlay);
    radarOverlay = null;
  }

  async function loadRadarLayer() {
    stopRadarLayer();
    const request = radarRequest;
    const slug = currentSlug();
    const isCurrent = () => request === radarRequest && slug === currentSlug();
    const data = await api('/api/projects/' + slug + '/radar/').catch(error => {
      if (isCurrent()) throw error;
      return null;
    });
    if (!isCurrent()) return null;
    if (!data?.overlay?.url) {
      const failure = new Error('雷達影像無法使用');
      failure.status = 503;
      failure.payload = data;
      throw failure;
    }

    const overlay = L.imageOverlay(data.overlay.url, data.overlay.bounds, {opacity: .52, interactive: false, className: 'radar-layer'});
    radarOverlay = overlay;
    return new Promise((resolve, reject) => {
      const onLoad = () => finish(isCurrent() ? data : null);
      const onError = () => finish(null, isCurrent() ? new Error('雷達影像無法載入') : null);
      const timer = setTimeout(onError, 10000);

      function finish(result, error = null) {
        clearTimeout(timer);
        overlay.off('load', onLoad).off('error', onError);
        cancelRadarLoad = null;
        if (error) {
          radarGroup.removeLayer(overlay);
          radarOverlay = null;
          reject(error);
        } else resolve(result);
      }

      cancelRadarLoad = () => finish(null);
      overlay.once('load', onLoad).once('error', onError).addTo(radarGroup);
    });
  }

  onSelector('[data-live="radar"]', 'change', async event => {
    const control = event.currentTarget;
    const status = $('live-radar-status');
    if (!control.checked) {
      stopRadarLayer();
      status.textContent = '待命';
      return;
    }
    status.textContent = '雷達影像載入中';
    try {
      const data = await loadRadarLayer();
      if (data && control.checked) {
        status.textContent = (data.stale ? '過期快取｜' : '') + '影像已載入｜來源影像時間未確認';
      }
    } catch (error) {
      if (control.checked) status.textContent = '雷達｜' + (error.status ? cwaFailure(error) : error.message);
    }
  });
  let dynamicFloodOverlay = null;
  let dynamicFloodGeneration = 0;
  function dynamicFloodStatusText(data) {
    if (!data.overlay?.image_url) {
      const states = {
        disabled: '動態淹水服務已停用',
        empty: '本次查詢未回傳淹水影像',
        timeout: '動態淹水資料查詢逾時',
        unverified: data.reason === 'project_outside_source_extent'
          ? '影像範圍未涵蓋工區搜尋中心' : '動態淹水影像與範圍無法核對',
      };
      return states[data.status] || '動態淹水資料暫時無法使用';
    }
    const stale = data.freshness === 'stale';
    const mode = data.status === 'fallback' ? '0 小時回退' : data.forecast_hours + ' 小時';
    const layerState = stale ? mode + '｜過期快取' : '已載入｜' + mode;
    const coverage = data.project_coverage === 'unknown' ? '｜工區涵蓋未知' : '';
    const source = data.source_time ? '｜來源時間 ' + new Date(data.source_time).toLocaleString('zh-TW') : '';
    return layerState + '｜' + data.region + coverage + source + '｜來源有效期未核實';
  }
  async function loadDynamicFloodLayer(forecastHours = 2) {
    stopDynamicFloodLayer();
    const generation = dynamicFloodGeneration;
    const slug = currentSlug();
    const status = $('live-dynamic-flood-status');
    const current = () => generation === dynamicFloodGeneration && slug === currentSlug();
    status.textContent = '載入中';
    try {
      const response = await fetch('/api/projects/' + encodeURIComponent(slug) + '/dynamic-flood/?forecastHours=' + encodeURIComponent(forecastHours));
      const data = await response.json();
      if (!current()) return;
      if (!data.overlay?.image_url) {
        status.textContent = dynamicFloodStatusText(data);
        return;
      }
      status.textContent = '影像載入中';
      const overlay = L.imageOverlay(data.overlay.image_url, data.overlay.bounds, {
        opacity: .48, interactive: false, className: 'dynamic-flood-layer',
      });
      dynamicFloodOverlay = overlay;
      overlay.once('load', () => {
        if (current() && dynamicFloodOverlay === overlay) status.textContent = dynamicFloodStatusText(data);
      });
      overlay.once('error', () => {
        if (!current() || dynamicFloodOverlay !== overlay) return;
        stopDynamicFloodLayer();
        status.textContent = '動態淹水影像載入失敗';
      });
      overlay.addTo(dynamicFloodGroup);
    } catch (_) {
      if (!current()) return;
      stopDynamicFloodLayer();
      status.textContent = '動態淹水資料暫時無法使用';
    }
  }
  function stopDynamicFloodLayer() {
    dynamicFloodGeneration += 1;
    dynamicFloodOverlay = null;
    dynamicFloodGroup.clearLayers();
    const status = $('live-dynamic-flood-status');
    if (status) status.textContent = '待命';
  }
  onSelector('[data-live="dynamic-flood"]', 'change', event => {
    if (event.currentTarget.checked) void loadDynamicFloodLayer();
    else stopDynamicFloodLayer();
  });

  function trafficColor(level) { return ({'-1': '#8b1e1e', '0': '#8b8b8b', '1': '#2e9d59', '2': '#e0b341', '3': '#e4772f', '4': '#c43d3d', '5': '#8b1e1e'})[String(level)] || '#8b8b8b'; }
  let trafficTimer = null;
  let trafficRequest = 0;
  function stopHighwayTraffic() {
    trafficRequest++;
    if (trafficTimer) clearInterval(trafficTimer);
    trafficTimer = null;
    trafficGroup.clearLayers();
  }
  async function loadHighwayTraffic() {
    const token = ++trafficRequest;
    const slug = currentSlug();
    const status = $('live-traffic-status');
    const current = () => token === trafficRequest && slug === currentSlug();
    try {
      const data = await api('/api/projects/' + slug + '/highway-traffic/?limit=300');
      if (!current()) return;
      trafficGroup.clearLayers();
      L.geoJSON(data, {
        style: feature => ({ color: trafficColor(feature.properties?.congestion_level), weight: 5, opacity: .9 }),
        onEachFeature: (feature, item) => {
          const p = feature.properties || {};
          item.bindTooltip((p.road_name || '省道') + '｜' + (p.section_name || p.section_id || '') +
            '｜速度 ' + (p.travel_speed ?? '—') + ' km/h｜來源時間 ' + cwaTime(p.data_collect_time));
        }
      }).addTo(trafficGroup);
      status.textContent = runtimeStatusLabel(data.runtime_state) +
        (data.status === 'degraded' && data.runtime_state === 'available' ? '｜部分來源不可用' : '');
    } catch (_) {
      if (!current()) return;
      trafficGroup.clearLayers();
      status.textContent = '路況服務暫不可用';
    }
  }

  new MutationObserver(() => { $('cctv-layer')?.querySelectorAll('small').forEach(node => node.remove()); const parent = $('weather-layer')?.parentElement; if (parent) parent.insertBefore($('cctv-layer'), $('weather-layer')); if ($('cctv-layer') && $('cctv-viewer') && $('cctv-viewer').parentElement !== $('cctv-layer')) $('cctv-layer').appendChild($('cctv-viewer')); }).observe($('cctv-layer'), { childList: true, subtree: true });

  async function loadLayers() { const data = await api('/api/layers/'); const list = $('layer-list'); list.replaceChildren(); data.layers.forEach(item => { const wrap = document.createElement('div'); wrap.className = 'layer-control'; const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; label.append(check, textNode('span', item.name)); const range = document.createElement('input'); range.type = 'range'; range.min = 0; range.max = 100; range.value = 70; const state = textNode('span', '待載入', 'layer-state'); check.addEventListener('change', async () => { if (!check.checked) { if (overlayLayers[item.slug]) map.removeLayer(overlayLayers[item.slug]); delete activeLayerItems[item.slug]; state.textContent = '已隱藏'; return; } activeLayerItems[item.slug] = item; item.queryState = state; state.textContent = '載入中'; try { await refreshLayer(item, range.value / 100); } catch (error) { check.checked = false; state.textContent = '載入失敗'; } }); range.addEventListener('input', () => overlayLayers[item.slug]?.setStyle({ fillOpacity: range.value / 100 * .28, opacity: range.value / 100 })); wrap.append(label, range, state); list.appendChild(wrap); }); }
  const mrtLineColors = { '臺北捷運': { '文湖線': '#9E652E', '淡水信義線': '#E3002C', '松山新店線': '#008559', '中和新蘆線': '#FFA300', '板南線': '#0070BD', '環狀線': '#FFD300' }, '桃園捷運': { '機場捷運': '#3A0078', '綠線': '#84BD00', '棕線': '#8B4513' }, '臺灣桃園國際機場捷運': { '機場捷運': '#3A0078' }, '高雄捷運': { '紅線': '#E3002D', '橘線': '#F6821F', '環狀輕軌': '#72B332', '環狀線': '#72B332' } };
  const mrtFallbackColors = { '文湖線': '#9E652E', '淡水信義線': '#E3002C', '松山新店線': '#008559', '中和新蘆線': '#FFA300', '板南線': '#0070BD', '環狀線': '#FFD300', '三鶯線': '#B05A3C', '紅線': '#E3002D', '橘線': '#F6821F', '綠線': '#84BD00', '棕線': '#8B4513', '機場捷運': '#3A0078', '環狀輕軌': '#72B332' };
  Object.assign(mrtFallbackColors, { '三鶯線': '#79BCE8', '淡海輕軌': '#E5554F', '淡海輕軌綠山線': '#E5554F', '淡海輕軌藍海線': '#E5554F', '安坑輕軌': '#A28D5B', '桃園綠線': '#84BD00' });
  Object.values(mrtLineColors).forEach(colors => Object.assign(colors, { '三鶯線': '#79BCE8', '淡海輕軌': '#E5554F', '安坑輕軌': '#A28D5B', '桃園綠線': '#84BD00', '綠線': '#84BD00' }));
  function geologicViewportBounds() { const bounds = map.getBounds(); const southWest = bounds.getSouthWest(); const northEast = bounds.getNorthEast(); return [southWest.lng, southWest.lat, northEast.lng, northEast.lat].join(','); }
  function localScanNotice(quality) {
    if (quality?.scan_incomplete) return '｜掃描未完成';
    if (quality?.display_truncated) return '｜顯示已截斷';
    return '';
  }
  function setGeologicSensitiveStatus(message) { if (geologicSensitiveControl?.state) geologicSensitiveControl.state.textContent = message; }
  function cancelGeologicSensitiveRefresh() { if (geologicViewportTimer) { clearTimeout(geologicViewportTimer); geologicViewportTimer = null; } if (geologicViewportController) { geologicViewportController.abort(); geologicViewportController = null; } geologicViewportRequestSequence += 1; }
  function geologicViewportErrorMessage(error) { if (error.status === 401) return '請先登入以載入地質敏感區'; if (error.status === 403) return '目前沒有載入地質敏感區的權限'; if (error.status === 400) return '目前地圖範圍無法查詢地質敏感區'; if (error.status === 503) return '地質敏感區索引暫時無法使用'; return '地質敏感區載入失敗，請稍後再試'; }
  async function refreshGeologicSensitiveLayer(opacity) {
    const requestSequence = ++geologicViewportRequestSequence;
    if (geologicViewportController) geologicViewportController.abort();
    const controller = new AbortController();
    geologicViewportController = controller;
    setGeologicSensitiveStatus('載入中');
    const query = new URLSearchParams({ bbox: geologicViewportBounds(), limit: '1000' });
    try {
      const response = await fetch('/api/layers/geologic_sensitive_areas/viewport/?' + query.toString(), { headers: { Accept: 'application/json' }, signal: controller.signal });
      const data = response.headers.get('content-type')?.includes('json') ? await response.json() : null;
      if (!response.ok) { const error = new Error(data?.error?.message || '地質敏感區載入失敗'); error.status = response.status; throw error; }
      if (requestSequence !== geologicViewportRequestSequence) return;
      if (geologicSensitiveLayer) map.removeLayer(geologicSensitiveLayer);
      geologicSensitiveLayer = L.geoJSON(data, { style: { color: '#d97745', weight: 2, opacity, fillColor: '#d97745', fillOpacity: opacity * .2 }, onEachFeature: (feature, layer) => { const properties = feature.properties || {}; layer.bindTooltip(properties.official_name || properties.official_id || '地質敏感區'); } }).addTo(map);
      overlayLayers.geologic_sensitive_areas = geologicSensitiveLayer;
      setGeologicSensitiveStatus('已載入 ' + (data.features || []).length + ' 筆' + localScanNotice(data.data_quality));
    } catch (error) {
      if (error.name === 'AbortError' || requestSequence !== geologicViewportRequestSequence) return;
      setGeologicSensitiveStatus(geologicViewportErrorMessage(error));
    } finally {
      if (requestSequence === geologicViewportRequestSequence) geologicViewportController = null;
    }
  }
  function scheduleGeologicSensitiveRefresh(opacity) { if (geologicViewportTimer) clearTimeout(geologicViewportTimer); geologicViewportTimer = setTimeout(() => { geologicViewportTimer = null; refreshGeologicSensitiveLayer(opacity); }, 300); }
  function createGeologicSensitiveControl() {
    if (geologicSensitiveControl || !$('layer-list')) return;
    const wrap = document.createElement('div'); wrap.className = 'layer-control'; wrap.dataset.geologicSensitiveControl = 'true';
    const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; check.setAttribute('aria-label', '地質敏感區'); label.append(check, textNode('span', '地質敏感區'));
    const range = document.createElement('input'); range.type = 'range'; range.min = 0; range.max = 100; range.value = 70; range.setAttribute('aria-label', '地質敏感區透明度');
    const state = textNode('span', '未啟用', 'layer-state');
    check.addEventListener('change', () => { if (!check.checked) { cancelGeologicSensitiveRefresh(); if (geologicSensitiveLayer) map.removeLayer(geologicSensitiveLayer); geologicSensitiveLayer = null; delete overlayLayers.geologic_sensitive_areas; setGeologicSensitiveStatus('已隱藏'); return; } scheduleGeologicSensitiveRefresh(range.value / 100); });
    range.addEventListener('input', () => { const opacity = range.value / 100; geologicSensitiveLayer?.setStyle?.({ opacity, fillOpacity: opacity * .2 }); });
    wrap.append(label, range, state); $('layer-list').appendChild(wrap); geologicSensitiveControl = { check, range, state };
  }
  function synchronizeGeologicSensitiveControl() { document.querySelectorAll('.layer-control').forEach(node => { if (node.dataset.geologicSensitiveControl === 'true') return; const label = node.querySelector('label span')?.textContent || ''; if (label === 'geologic_sensitive_areas' || label === '地質敏感區') node.remove(); }); createGeologicSensitiveControl(); }
  const geologicControlObserver = new MutationObserver(synchronizeGeologicSensitiveControl);
  geologicControlObserver.observe($('layer-list'), { childList: true });
  function transportLayerColor(item, feature) { if (item.slug === 'rail_lines') return '#2182CC'; if (item.slug !== 'mrt_lines') return item.category === 'hazards' ? '#b94b45' : item.category === 'transport' ? '#d9c5b2' : '#7e7f83'; const properties = feature?.properties || {}; const operatorColors = mrtLineColors[properties.MRTSYS] || {}; return operatorColors[properties.MRTCODE] || mrtFallbackColors[properties.MRTCODE] || '#7e7f83'; }
  const baseTransportLayerColor = function (item, feature) { if (item.slug === 'rail_lines') return '#2182CC'; if (item.slug !== 'mrt_lines') return item.category === 'hazards' ? '#b94b45' : item.category === 'transport' ? '#d9c5b2' : '#7e7f83'; const properties = feature?.properties || {}; const operatorColors = mrtLineColors[properties.MRTSYS] || {}; return operatorColors[properties.MRTCODE] || mrtFallbackColors[properties.MRTCODE] || '#7e7f83'; };
  function transportLayerColor(item, feature) { const properties = feature?.properties || {}; if (item.slug === 'mrt_lines' && String(properties.MRTCODE || '').includes('淡海輕軌')) return '#E5554F'; if (item.slug === 'mrt_lines' && String(properties.MRTCODE || '').includes('輕軌')) return '#72B332'; return baseTransportLayerColor(item, feature); }
  function mergeFloodLayerControl() {
    if (document.querySelector('[data-flood-selector]')) return;
    const staticFloodLayers = [
      ['flood_6h_150mm', '6 小時／150 毫米'], ['flood_6h_250mm', '6 小時／250 毫米'],
      ['flood_6h_350mm', '6 小時／350 毫米'], ['flood_12h_200mm', '12 小時／200 毫米'],
      ['flood_12h_300mm', '12 小時／300 毫米'], ['flood_12h_400mm', '12 小時／400 毫米'],
      ['flood_24h_200mm', '24 小時／200 毫米'], ['flood_24h_350mm', '24 小時／350 毫米'],
      ['flood_24h_500mm', '24 小時／500 毫米'], ['flood_650mm_24h', '24 小時／650 毫米']
    ];
    const controls = [...document.querySelectorAll('.layer-control')].filter(node => /flood_(?:6h|12h|24h)_\d+mm|flood_650mm_24h|淹水範圍|毫米|650.*24/i.test(node.textContent));
    if (!controls.length) return;
    controls.forEach(node => { node.hidden = true; });
    const wrap = document.createElement('div'); wrap.className = 'layer-control flood-layer-control'; wrap.dataset.floodSelector = 'true';
    const label = document.createElement('label');
    const toggle = document.createElement('input'); toggle.type = 'checkbox'; toggle.className = 'flood-toggle'; toggle.setAttribute('aria-label', '顯示淹水範圍');
    label.append(toggle, textNode('span', '淹水範圍'));
    const select = document.createElement('select');
    staticFloodLayers.forEach(([slug, name]) => { const option = document.createElement('option'); option.value = slug; option.textContent = name; select.appendChild(option); }); label.appendChild(select);
    const range = document.createElement('input'); range.type = 'range'; range.min = 0; range.max = 100; range.value = 48; range.setAttribute('aria-label', '淹水圖層透明度');
    const state = textNode('span', '待載入', 'layer-state');
    async function applySelection(value) {
      staticFloodLayers.forEach(([slug]) => { if (overlayLayers[slug]) { map.removeLayer(overlayLayers[slug]); delete overlayLayers[slug]; } });
      if (!toggle.checked) { state.textContent = '已隱藏'; return; }
      state.textContent = '載入中';
      try {
        overlayLayers[value] = await refreshLayer({ slug: value, delivery: 'bbox', category: 'hazards', queryState: state }, range.value / 100);
      } catch (_) { state.textContent = '載入失敗'; toggle.checked = false; }
    }
    toggle.addEventListener('change', () => applySelection(select.value));
    select.addEventListener('change', () => { if (toggle.checked) applySelection(select.value); });
    range.addEventListener('input', () => { const opacity = range.value / 100; const slug = select.value; overlayLayers[slug]?.setStyle?.({ fillOpacity: opacity * .28, opacity }); });
    wrap.append(label, range, state); $('layer-list').appendChild(wrap);
  }
  setInterval(mergeFloodLayerControl, 300);
  const STATION_VISIBILITY_DISTANCE_METERS = 5000;
  const SCALE_REFERENCE_PIXELS = 100;
  function stationsVisibleAtCurrentScale() {
    const size = map.getSize();
    if (!size || !size.x || !size.y) return true;
    const centerPoint = L.point(size.x / 2, size.y / 2);
    const center = map.containerPointToLatLng(centerPoint);
    const referencePoint = map.containerPointToLatLng(L.point(centerPoint.x + SCALE_REFERENCE_PIXELS, centerPoint.y));
    return map.distance(center, referencePoint) < STATION_VISIBILITY_DISTANCE_METERS;
  }
  function updateStationVisibility() {
    const visible = stationsVisibleAtCurrentScale();
    mrtStationMarkers.forEach(marker => { marker._stationBaseOpacity ??= marker.options.opacity ?? 1; marker.setOpacity(visible ? marker._stationBaseOpacity : 0); });
    traStationMarkers.forEach(marker => { marker._stationBaseOpacity ??= marker.options.opacity ?? 1; marker.setOpacity(visible ? marker._stationBaseOpacity : 0); });
  }
  let traStationMarkers = [];
  function traStationCode(properties) { return String(properties.station_code || properties.StationCode || properties.station_id || properties.OBJECTID || '').trim(); }
  function traStationName(properties) { const raw = String(properties.station_name || properties.MARKNAME1 || properties.MARKNAME2 || '臺鐵車站'); return raw.replace(/^臺鐵/, '').replace(/^台鐵/, '').replace(/[-_].*$/, '').trim() || '臺鐵車站'; }
  function traStationLabel(name) { return String(name || '').replace(/(?:車站|站)$/u, '') || name; }
  function traStationIcon(code, name, scale, color) { const width = 132 * scale, height = 36 * scale; return L.divIcon({ className: 'tra-station-marker', html: '<span style="--tra-line-color:' + color + ';width:' + width + 'px;height:' + height + 'px;font-size:' + Math.max(8, 16 * scale) + 'px">' + name + '</span>', iconSize: [width, height], iconAnchor: [width / 2, height / 2] }); }
  function renderTraStations(data, opacity) { const group = L.layerGroup(); traStationMarkers = []; const scale = Math.max(.15, Math.min(1.6, Math.pow(2, map.getZoom() - 15))); (data.features || []).forEach(feature => { const coordinates = feature.geometry?.coordinates; if (!coordinates || feature.geometry.type !== 'Point') return; const properties = feature.properties || {}; const code = traStationCode(properties); const name = traStationName(properties); const label = traStationLabel(name); const marker = L.marker([coordinates[1], coordinates[0]], { icon: traStationIcon(code, label, scale, '#2182CC'), opacity }).bindTooltip('<b>' + name + '</b><br><small>車站代號：' + code + '</small>', { direction: 'top', sticky: true }); marker._traStationCode = code; marker._traStationName = label; marker.addTo(group); traStationMarkers.push(marker); }); return group.addTo(map); }
  function updateTraStationScale() { const scale = Math.max(.15, Math.min(1.6, Math.pow(2, map.getZoom() - 15))); traStationMarkers.forEach(marker => marker.setIcon(traStationIcon(marker._traStationCode, marker._traStationName, scale, '#2182CC'))); }
  async function refreshLayer(item, opacity) { let url = '/api/layers/' + item.slug + '/'; if (item.delivery === 'bbox') url += '?bbox=' + encodeURIComponent(map.getBounds().toBBoxString()) + '&limit=300'; const data = await api(url); if (item.queryState) item.queryState.textContent = '已載入' + localScanNotice(data.data_quality); if (overlayLayers[item.slug]) map.removeLayer(overlayLayers[item.slug]); if (item.slug === 'tra_stations') { overlayLayers[item.slug] = renderTraStations(data, opacity); return overlayLayers[item.slug]; } overlayLayers[item.slug] = L.geoJSON(data, { style: feature => ({ color: transportLayerColor(item, feature), weight: item.slug === 'rail_lines' || item.slug === 'mrt_lines' ? 3 : 2, opacity, fillOpacity: opacity * .28 }) }).addTo(map); return overlayLayers[item.slug]; }
  const MIN_BBOX_SPAN = 0.000001;
  const MAX_BBOX_SPAN = 1.5;
  let geologyBboxReason = 'not_ready';

  function geologyBbox() {
    geologyBboxReason = 'not_ready';
    const container = map.getContainer();
    if (!map._loaded || !(container.clientWidth > 0) || !(container.clientHeight > 0)) return null;
    map.invalidateSize({ pan: false });
    const bounds = map.getBounds();
    const southWest = bounds.getSouthWest();
    const northEast = bounds.getNorthEast();
    if (!Number.isFinite(southWest.lng) || !Number.isFinite(southWest.lat)
      || !Number.isFinite(northEast.lng) || !Number.isFinite(northEast.lat)) {
      geologyBboxReason = 'invalid';
      return null;
    }
    if (!(southWest.lng < northEast.lng && southWest.lat < northEast.lat)) {
      geologyBboxReason = 'invalid';
      return null;
    }
    const lngSpan = northEast.lng - southWest.lng;
    const latSpan = northEast.lat - southWest.lat;
    if (!(lngSpan >= MIN_BBOX_SPAN && latSpan >= MIN_BBOX_SPAN)) {
      geologyBboxReason = 'too_small';
      return null;
    }
    if (lngSpan > MAX_BBOX_SPAN || latSpan > MAX_BBOX_SPAN) {
      geologyBboxReason = 'too_large';
      return null;
    }
    geologyBboxReason = null;
    return bounds.toBBoxString();
  }
  function geologyRequestUrl(path) {
    const bbox = geologyBbox();
    if (!bbox) return null;
    return path + '?mode=exploration&bbox=' + encodeURIComponent(bbox);
  }
  const GEOLOGY_MOVE_DEBOUNCE_MS = 400;
  let geologyMoveTimer = null, screeningRefreshPending = false, screeningRadius = 0;
  const publishedGeologyArea = L.geoJSON(null, {
    pmIgnore: true, interactive: false, style: {color: '#6abf85', weight: 3, fillOpacity: .1}
  }).addTo(map);
  const geologyBufferArea = L.geoJSON(null, {
    pmIgnore: true, interactive: false, style: {color: '#6abf85', weight: 2, fill: false, dashArray: '5 5'}
  }).addTo(map);
  const geologySummaryRequest = { sequence: 0, key: null, promise: null, controller: null };
  const geologyLayerRequests = {};
  const geologyGenerations = new Map();
  let displayedGeologySource = null;

  function acceptGeologySource(slug, data) {
    if (slug !== currentSlug()) return false;
    const previous = geologyGenerations.get(slug) ?? -1;
    if (data.worksiteGeneration < previous) return false;
    geologyGenerations.set(slug, data.worksiteGeneration);
    if (previous >= 0 && data.worksiteGeneration > previous) scheduleGeologyRefresh();
    const displayKey = [slug, data.worksiteGeneration, data.worksiteReference, data.worksiteReason].join('|');
    if (displayKey !== displayedGeologySource) {
      displayedGeologySource = displayKey;
      $('geology-layer-screening-results')?.replaceChildren();
      $('geology-summary-results')?.replaceChildren();
      $('geology-summary-compact')?.replaceChildren();
      publishedGeologyArea.clearLayers();
      geologyBufferArea.clearLayers();
      for (const layerName of Object.keys(geologyLayerControls)) {
        const key = 'geology-' + layerName;
        if (overlayLayers[key]) map.removeLayer(overlayLayers[key]);
      }
    }
    const source = $('geology-worksite-source');
    if (!source) return true;
    if (data.worksiteReference) {
      const [status, planId, version] = data.worksiteReference.split(':');
      source.textContent = (status === 'legacy_unverified' ? '暫行地質比對，舊發布審核未驗證' : '正式地質檢核來源') + '｜工區 ' + planId + ' v' + version;
    } else {
      const reasons = {
        source_archived: '原工區已封存，請由審核者重新選定來源。',
        ambiguous_source: '有多筆發布工區，請由審核者選定來源。',
        source_not_selected: '請由審核者選定正式來源。',
        invalid_worksite_geometry: '發布範圍缺少有效面幾何。',
        invalid_source: '無法辨識發布來源。',
      };
      source.textContent = '正式檢核暫不可用｜' + (reasons[data.worksiteReason] || '尚無正式工區範圍。');
    }
    return true;
  }
  function geologyRequestKey(layerName, bbox, limit) {
    const projectSlug = currentSlug();
    return [projectSlug, geologyGenerations.get(projectSlug), layerName, bbox, limit].join('|');
  }
  function geologyLayerRequestState(layerName) {
    geologyLayerRequests[layerName] ||= { sequence: 0, key: null, promise: null, controller: null };
    return geologyLayerRequests[layerName];
  }
  function startGeologyRequest(path, key, state) {
    if (state.key === key && state.promise) return { promise: state.promise, sequence: state.sequence, reused: true };
    state.controller?.abort();
    state.sequence += 1;
    const request = { sequence: state.sequence };
    const controller = new AbortController();
    state.key = key;
    state.controller = controller;
    const promise = api(path, { signal: controller.signal, cache: "no-store" }, true);
    state.promise = promise;
    request.promise = promise;
    request.promise.then(
      () => { if (state.promise === promise) { state.promise = null; state.controller = null; } },
      () => { if (state.promise === promise) { state.promise = null; state.controller = null; } }
    );
    return request;
  }
  function scheduleGeologyRefresh(includeScreening = true) {
    if (includeScreening !== false) screeningRefreshPending = true;
    clearTimeout(geologyMoveTimer);
    geologyMoveTimer = setTimeout(() => {
      geologyMoveTimer = null;
      if (screeningRefreshPending) {
        screeningRefreshPending = false;
        refreshGeologySummary();
      }
      Object.entries(geologyLayerControls)
        .filter(([, control]) => control.check.checked)
        .forEach(([layerName, control]) => refreshGeologyLayer(layerName, control.range.value / 100));
    }, GEOLOGY_MOVE_DEBOUNCE_MS);
  }
  window.addEventListener('focus', scheduleGeologyRefresh);
  on('geology-screening-form', 'submit', event => {
    event.preventDefault();
    const radius = Number($('geology-buffer-input').value);
    if (Number.isFinite(radius) && radius >= 0) {
      screeningRadius = radius;
      refreshGeologySummary();
    }
  });

  function geologyBboxBlockedText() {
    return geologyBboxReason === 'too_large'
      ? '地圖範圍過大，請放大地圖後再載入工程地質資料。'
      : '地圖尚未完成初始化，暫不載入地質資料';
  }
  function geologyPopup(feature, layer) {
    const properties = feature.properties || {};
    const content = document.createElement('div');
    content.appendChild(textNode('b', geologyLayerItems[feature._geologyLayer]?.label || '地質雲資料'));
    ['Project_No', 'Project_Name', 'Hole_Point_No', 'Depth', 'classify', 'area'].filter(key => properties[key] !== undefined && properties[key] !== null && properties[key] !== '').forEach(key => content.appendChild(textNode('div', key + '：' + properties[key])));
    content.appendChild(textNode('small', '來源：地質雲｜唯讀初步檢核'));
    layer.bindPopup(content);
  }
  function geologySummaryLoading(box) {
    box.classList.add('is-loading');
    let loading = box.querySelector('.geology-summary-loading');
    if (!loading) { loading = textNode('small', '更新中…', 'geology-summary-loading muted'); box.prepend(loading); }
  }
  function geologySummaryError(box, error) {
    box.classList.remove('is-loading');
    let message = box.querySelector('.geology-summary-error');
    if (!message) { message = textNode('small', '', 'geology-summary-error muted'); box.prepend(message); }
    message.textContent = '摘要更新失敗：' + error.message;
  }
  function geologyQualityText(quality) {
    if (!quality) return '資料狀態未知';
    const notes = [];
    const reason = {
      network_failure: '連線失敗', timeout: '查詢逾時', invalid_response: '回應格式錯誤',
      invalid_crs: '座標無法驗證', unsupported_crs: '座標系統不支援', invalid_geometry: '幾何無法驗證',
      missing_administrative_area: '缺少已確認行政區', unverified_administrative_area: '行政區來源未驗證',
      administrative_coverage_unknown: '行政區覆蓋未確認', source_archived: '正式來源已封存',
      no_worksite_geometry: '尚無正式工區範圍', invalid_worksite_geometry: '工區幾何無法驗證',
      ambiguous_source: '有多筆發布工區，尚未選定來源', source_not_selected: '尚未選定正式來源',
    }[quality.failure_reason];
    if (quality.retrieval === 'empty') notes.push('本次查詢未回傳相關圖徵');
    if (quality.retrieval === 'unavailable') notes.push('資料不可用');
    if (quality.retrieval === 'error') notes.push('取得失敗');
    if (quality.cache === 'stale_cache') notes.push('使用過期快取');
    else if (quality.cache === 'fresh_cache') notes.push('使用新鮮快取');
    if (reason) notes.push(reason);
    if (quality.completeness?.display_truncated) notes.push('顯示已截斷');
    if (quality.completeness?.scan_incomplete) notes.push('掃描不完整');
    if (quality.completeness?.upstream_coverage_unknown) notes.push('上游覆蓋未知');
    if (quality.completeness?.upstream_limit_unknown) notes.push('上游上限未知');
    return notes.join('｜') || '已取得資料';
  }

  function renderGeologySummary(data) {
    const box = $('geology-summary-results');
    const compact = $('geology-summary-compact');
    const context = data.screening_context || {};
    if (!box || !compact) return;

    box.classList.remove('is-loading');
    box.replaceChildren();
    compact.replaceChildren();
    publishedGeologyArea.clearLayers();
    geologyBufferArea.clearLayers();
    if (context.geometry) publishedGeologyArea.addData(context.geometry);
    if (context.buffer_geometry) geologyBufferArea.addData(context.buffer_geometry);
    if (context.published_version != null) {
      const range = context.buffer_m ? '綠色虛線：工區外 ' + context.buffer_m + ' m' : '僅檢核工區本體（0 m）';
      compact.appendChild(textNode('p', '綠色實線：發布工區 v' + context.published_version + '｜' + range, 'data-item'));
    } else {
      compact.appendChild(textNode('p', '目前無可用的正式工區檢核', 'muted'));
    }
    box.appendChild(textNode('p', '產生時間 ' + (data.generated_at ? new Date(data.generated_at).toLocaleString('zh-TW') : '未知'), 'data-item'));
    for (const item of data.layers || []) {
      const label = geologyLayerItems[item.layer]?.label || item.layer;
      const quality = item.data_quality;
      const screening = item.screening || {};
      const state = geologyQualityText(quality);
      compact.appendChild(textNode('div', label + '｜' + state, 'data-item'));
      const card = textNode('div', '', 'data-item');
      const relation = typeof screening.intersects === 'boolean' ? (screening.intersects ? '有相交或接觸' : '已取得圖徵未見相交') : '無法計算';
      card.append(textNode('b', label), textNode('small', state),
        textNode('small', '工區相交：' + relation + '｜最近距離：' + (screening.nearest_distance_m == null ? '無法計算' : screening.nearest_distance_m + ' m')));
      if (item.layer === 'liquefaction') {
        const area = screening.intersection_area_m2;
        const ratio = screening.intersection_ratio;
        card.appendChild(textNode('small', '工區重疊：' + (area == null || ratio == null ? '無法計算' : area.toLocaleString() + ' m²／' + (ratio * 100).toFixed(2) + '%')));
      }
      if (screening.nearest_drill?.properties) {
        const drill = screening.nearest_drill.properties;
        card.appendChild(textNode('small', '最近鑽孔：' + (drill.Hole_Point_No ?? '未知')));
      }
      card.appendChild(textNode('small', '來源 ' + (quality?.source || '地質雲') +
        '｜資料發布日期 ' + (quality?.source_published_at || '未知') +
        '｜系統取得時間 ' + (quality?.fetched_at ? new Date(quality.fetched_at).toLocaleString('zh-TW') : '未知')));
      if (quality?.dataset_version) card.appendChild(textNode('small', '來源版本 ' + quality.dataset_version));
      box.appendChild(card);
    }
  }
  async function refreshGeologySummary() {
    const box = $('geology-summary-results'); if (!box) return;
    const slug = currentSlug();
    if (!slug) return;
    const query = new URLSearchParams({mode: 'screening', buffer_m: String(screeningRadius), limit: '300'});
    const url = '/api/projects/' + encodeURIComponent(slug) + '/geology/summary/?' + query;
    const request = startGeologyRequest(url, geologyRequestKey('screening', screeningRadius, 300), geologySummaryRequest);
    if (request.reused) return;
    geologySummaryLoading(box);
    try {
      const data = await request.promise;
      if (request.sequence !== geologySummaryRequest.sequence || !acceptGeologySource(slug, data)) return;
      renderGeologySummary(data);
    } catch (error) {
      if (error.name === 'AbortError' || request.sequence !== geologySummaryRequest.sequence) return;
      geologySummaryError(box, error);
    }
  }
  function renderGeologyScreening(layerName, data) {
    const box = $('geology-layer-screening-results'); if (!box) return;
    let card = Array.from(box.querySelectorAll('[data-geology-screening-layer]')).find(node => node.dataset.geologyScreeningLayer === layerName);
    if (!card) { card = textNode('div', '', 'data-item'); card.dataset.geologyScreeningLayer = layerName; box.appendChild(card); }
    const screening = data.screening || {};
    card.replaceChildren(textNode('b', geologyLayerItems[layerName]?.label || layerName));
    card.append(textNode('small', '目前視窗探索｜工區相交：' + (typeof screening.intersects !== 'boolean' ? '無法計算' : screening.intersects ? '有相交或接觸' : '所見圖徵未見相交') + '｜最近距離：' + (screening.nearest_distance_m == null ? '—' : screening.nearest_distance_m + ' m') + '｜附近：' + (Number.isFinite(screening.nearby_feature_count) ? screening.nearby_feature_count + ' 筆' : '資料不可用')));
    if (screening.nearest_drill?.properties) { const p = screening.nearest_drill.properties; card.append(textNode('small', '最近鑽孔：' + (p.Project_No || '—') + '／' + (p.Hole_Point_No || '—') + '／Depth ' + (p.Depth ?? '—'))); }
  }
  async function refreshGeologyLayer(layerName, opacity) {
    const control = geologyLayerControls[layerName]; if (!control) return;
    const slug = currentSlug();
    if (!slug) return;
    const url = geologyRequestUrl('/api/projects/' + encodeURIComponent(currentSlug()) + '/geology/' + layerName + '/');
    if (!url) { control.state.textContent = geologyBboxBlockedText(); return; }
    const bbox = new URL(url, window.location.origin).searchParams.get('bbox');
    const state = geologyLayerRequestState(layerName);
    const request = startGeologyRequest(url + '&limit=300', geologyRequestKey('exploration-' + layerName, bbox, 300), state);
    if (request.reused) return;
    control.state.textContent = '更新中…';
    try {
      const data = await request.promise;
      if (request.sequence !== state.sequence || !acceptGeologySource(slug, data)) return;
      const definition = geologyLayerItems[layerName]; (data.features || []).forEach(feature => { feature._geologyLayer = layerName; });
      const key = 'geology-' + layerName; if (overlayLayers[key]) map.removeLayer(overlayLayers[key]);
      overlayLayers[key] = L.geoJSON({ type: 'FeatureCollection', features: data.features || [] }, { pointToLayer: (feature, latlng) => L.circleMarker(latlng, { radius: 6, color: definition.color, fillColor: definition.color, fillOpacity: opacity * .7, weight: 2 }), style: { color: definition.color, weight: 2, opacity, fillColor: definition.color, fillOpacity: opacity * .28 }, onEachFeature: geologyPopup }).addTo(map);
      control.state.textContent = geologyQualityText(data.data_quality); renderGeologyScreening(layerName, data);
    } catch (error) {
      if (error.name === 'AbortError' || request.sequence !== state.sequence) return;
      control.state.textContent = 'error｜' + error.message;
      renderGeologyScreening(layerName, { status: 'error', error: error.message });
    }
  }
  function createGeologyControls() {
    const list = $('layer-list'); if (!list || document.querySelector('[data-geology-control]')) return;
    Object.entries(geologyLayerItems).forEach(([layerName, definition]) => {
      const wrap = document.createElement('div'); wrap.className = 'layer-control'; wrap.dataset.geologyControl = 'true';
      const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; check.setAttribute('aria-label', definition.label); const swatch = textNode('span', '● ' + definition.label); swatch.style.color = definition.color; label.append(check, swatch);
      const range = document.createElement('input'); range.type = 'range'; range.min = 0; range.max = 100; range.value = 70; const state = textNode('span', 'off', 'layer-state'); wrap.append(label, range, state); list.appendChild(wrap); geologyLayerControls[layerName] = { check, range, state };
      check.addEventListener('change', () => { if (!check.checked) { if (overlayLayers['geology-' + layerName]) map.removeLayer(overlayLayers['geology-' + layerName]); const request = geologyLayerRequestState(layerName); request.controller?.abort(); request.sequence += 1; request.key = null; state.textContent = 'off'; return; } refreshGeologyLayer(layerName, range.value / 100); });
      range.addEventListener('input', () => overlayLayers['geology-' + layerName]?.setStyle?.({ opacity: range.value / 100, fillOpacity: range.value / 100 * .28 }));
    });
  }
  map.on('moveend', () => { Object.values(activeLayerItems).filter(item => item.delivery === 'bbox').forEach(item => refreshLayer(item, .7).catch(() => {})); scheduleGeologyRefresh(false); if (geologicSensitiveControl?.check.checked) scheduleGeologicSensitiveRefresh(geologicSensitiveControl.range.value / 100); });

  let mrtNetworkReady = false, mrtStationMarkers = [], mrtNetworkOpacity = .7;
  function mrtStationCode(properties) { if (properties.station_code) return String(properties.station_code).toUpperCase(); const raw = [properties.MARKNAME2, properties.MARKNAME1, properties.station_name].filter(Boolean).join(' '); return raw.match(/\b(?:A|BL|BR|G|O|R|Y|C)\d{1,3}\b/i)?.[0]?.toUpperCase() || raw.match(/\b[A-Z]{1,3}\d{1,3}\b/i)?.[0]?.toUpperCase() || String(properties.OBJECTID || ''); }
  function mrtStationColor(properties, code) { const name = String(properties.MARKNAME1 || properties.MARKNAME2 || ''); const operator = name.includes('高雄') ? '高雄捷運' : name.includes('桃園') || code.startsWith('A') ? '桃園捷運' : '臺北捷運'; const prefix = code.match(/^[A-Z]+/)?.[0] || ''; const line = { '臺北捷運': { BR: '#9E652E', R: '#E3002C', G: '#008559', O: '#FFA300', BL: '#0070BD', Y: '#FFD300' }, '桃園捷運': { A: '#3A0078', G: '#84BD00', BR: '#8B4513' }, '高雄捷運': { R: '#E3002D', O: '#F6821F', C: '#72B332' } }; return line[operator]?.[prefix] || '#7e7f83'; }
  function mrtStationIcon(entries, scale) { const width = 104 * scale, height = 30 * scale; const html = entries.map(entry => '<span style="--station-color:' + entry.color + ';width:' + (width / entries.length) + 'px;height:' + height + 'px;font-size:' + Math.max(8, 16 * scale) + 'px">' + entry.code + '</span>').join(''); return L.divIcon({ className: entries.length > 1 ? 'mrt-station-marker mrt-transfer-marker' : 'mrt-station-marker', html, iconSize: [width, height], iconAnchor: [width / 2, height / 2] }); }
  function mrtStationTooltip(entries) { const escape = value => String(value || '').replace(/[&<>\"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;' }[char])); return entries.map(entry => '<div class="mrt-station-tooltip"><b>' + escape(entry.code) + '</b><br>' + escape(entry.name || '捷運車站') + '<br><small>' + escape(entry.operator) + (entry.nameEn ? ' · ' + escape(entry.nameEn) : '') + '</small></div>').join('<hr>'); }
  function mrtStationColor(properties, code) { const operator = String(properties.operator || ''); const lineName = String(properties.line_name || properties.line || properties.MRTCODE || ''); if (lineName.includes('三鶯線')) return '#79BCE8'; if (lineName.includes('淡海輕軌')) return '#E5554F'; if (lineName.includes('安坑輕軌')) return '#A28D5B'; if (lineName.includes('桃園綠線')) return '#84BD00'; const prefix = code.match(/^[A-Z]+/)?.[0] || ''; if (operator.includes('\u9ad8\u96c4')) return { R: '#E3002D', O: '#F6821F', C: '#72B332' }[prefix] || '#72B332'; if (operator.includes('\u6843\u5712')) return { A: '#3A0078', G: '#84BD00', BR: '#8B4513' }[prefix] || '#3A0078'; if (operator.includes('\u81fa\u4e2d')) return '#84BD00'; return { BR: '#9E652E', R: '#E3002C', G: '#008559', O: '#FFA300', BL: '#0070BD', Y: '#FFD300' }[prefix] || '#7e7f83'; }
  function resolveMrtStationCollisions() { const occupied = []; mrtStationMarkers.slice().sort((a, b) => String(a._mrtCode).localeCompare(String(b._mrtCode))).forEach(marker => { const element = marker.getElement(); if (!element) return; const point = map.latLngToContainerPoint(marker.getLatLng()); const width = marker.options.icon.options.iconSize[0], height = marker.options.icon.options.iconSize[1]; const box = { left: point.x - width / 2, right: point.x + width / 2, top: point.y - height / 2, bottom: point.y + height / 2 }; const overlaps = occupied.some(item => box.left < item.right && box.right > item.left && box.top < item.bottom && box.bottom > item.top); element.style.visibility = overlaps ? 'hidden' : 'visible'; if (!overlaps) occupied.push(box); }); }
  function updateMrtStationScale() { const scale = Math.max(.15, Math.min(1.6, Math.pow(2, map.getZoom() - 15))); mrtStationMarkers.forEach(marker => marker.setIcon(mrtStationIcon(marker._mrtEntries, scale))); setTimeout(resolveMrtStationCollisions, 0); }
  function setMrtNetworkOpacity(opacity) { mrtNetworkOpacity = opacity; overlayLayers.mrt_network?.eachLayer(layer => { if (layer.setStyle) layer.setStyle({ opacity, fillOpacity: 0 }); if (layer.setOpacity) layer.setOpacity(opacity); }); }
  function groupMrtStationFeatures(features) { const groups = []; features.forEach(feature => { const coordinates = feature.geometry?.coordinates; if (!coordinates) return; const existing = groups.find(group => map.distance([group.latitude, group.longitude], [coordinates[1], coordinates[0]]) <= 120); if (existing) { existing.features.push(feature); existing.latitude = (existing.latitude + coordinates[1]) / 2; existing.longitude = (existing.longitude + coordinates[0]) / 2; } else groups.push({ features: [feature], latitude: coordinates[1], longitude: coordinates[0] }); }); return groups; }
  async function loadMrtNetwork(opacity) { const [lines, stations] = await Promise.all([api('/api/layers/mrt_lines/'), api('/api/layers/mrt_stations/')]); if (overlayLayers.mrt_network) map.removeLayer(overlayLayers.mrt_network); mrtStationMarkers = []; const group = L.layerGroup(); L.geoJSON(lines, { style: feature => ({ color: transportLayerColor({ slug: 'mrt_lines', category: 'transport' }, feature), weight: 3, opacity, fillOpacity: 0 }) }).addTo(group); groupMrtStationFeatures(stations.features || []).forEach(station => { const entries = [...new Map(station.features.map(feature => { const properties = feature.properties || {}; const code = mrtStationCode(properties); return [String(properties.operator || '') + code, { code, name: properties.station_name, nameEn: properties.station_name_en, operator: properties.operator || '捷運系統', color: mrtStationColor(properties, code) }]; })).values()].sort((a, b) => a.code.localeCompare(b.code)); const marker = L.marker([station.latitude, station.longitude], { icon: mrtStationIcon(entries, Math.max(.15, Math.min(1.6, Math.pow(2, map.getZoom() - 15)))) }).bindTooltip(mrtStationTooltip(entries), { direction: 'top', sticky: true }); marker._mrtEntries = entries; marker._mrtCode = entries[0]?.code || ''; marker.addTo(group); mrtStationMarkers.push(marker); }); overlayLayers.mrt_network = group.addTo(map); mrtNetworkReady = true; mrtNetworkOpacity = opacity; setTimeout(resolveMrtStationCollisions, 0); }
  function mergeMrtLayerControls() { if (document.querySelector('[data-mrt-network-control]')) return; const controls = [...document.querySelectorAll('.layer-control')].filter(node => /mrt_lines|mrt_stations|捷運路線|捷運車站/i.test(node.textContent)); if (controls.length < 2) return; controls.forEach(node => { node.hidden = true; }); const wrap = document.createElement('div'); wrap.className = 'layer-control'; wrap.dataset.mrtNetworkControl = 'true'; const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; label.append(check, textNode('span', '捷運路線／車站')); const range = document.createElement('input'); range.type = 'range'; range.min = 0; range.max = 100; range.value = 70; const state = textNode('span', '待載入', 'layer-state'); check.addEventListener('change', async () => { if (!check.checked) { if (overlayLayers.mrt_network) map.removeLayer(overlayLayers.mrt_network); state.textContent = '已隱藏'; return; } state.textContent = '載入中'; try { await loadMrtNetwork(range.value / 100); state.textContent = '已載入'; } catch (_) { check.checked = false; state.textContent = '載入失敗'; } }); range.addEventListener('input', () => setMrtNetworkOpacity(range.value / 100)); wrap.append(label, range, state); $('layer-list').appendChild(wrap); }
  function mergeTraLayerControls() { if (document.querySelector('[data-tra-network-control]')) return; const controls = [...document.querySelectorAll('.layer-control')].filter(node => /rail_lines|tra_stations|鐵道路線|臺鐵車站|台鐵路線/i.test(node.textContent)); if (controls.length < 2) return; controls.forEach(node => { node.hidden = true; }); const wrap = document.createElement('div'); wrap.className = 'layer-control'; wrap.dataset.traNetworkControl = 'true'; const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; label.append(check, textNode('span', '台鐵路線／車站')); const range = document.createElement('input'); range.type = 'range'; range.min = 0; range.max = 100; range.value = 70; const state = textNode('span', '待載入', 'layer-state'); check.addEventListener('change', async () => { if (!check.checked) { ['rail_lines', 'tra_stations'].forEach(slug => { if (overlayLayers[slug]) map.removeLayer(overlayLayers[slug]); }); state.textContent = '已隱藏'; return; } state.textContent = '載入中'; try { await Promise.all([refreshLayer({ slug: 'rail_lines', category: 'transport' }, range.value / 100), refreshLayer({ slug: 'tra_stations', category: 'transport' }, range.value / 100)]); state.textContent = '已載入'; } catch (_) { check.checked = false; state.textContent = '載入失敗'; } }); range.addEventListener('input', () => { const opacity = range.value / 100; const lines = overlayLayers.rail_lines; if (lines?.setStyle) lines.setStyle({ opacity }); overlayLayers.tra_stations?.eachLayer(layer => layer.setOpacity?.(opacity)); }); wrap.append(label, range, state); $('layer-list').appendChild(wrap); }
  setInterval(mergeMrtLayerControls, 300);
  setInterval(mergeTraLayerControls, 300);
  map.on('zoomend moveend', () => { updateMrtStationScale(); updateTraStationScale(); updateStationVisibility(); setTimeout(resolveMrtStationCollisions, 0); });
  setInterval(updateStationVisibility, 250);

  const layerLabels = { township: '行政區界', debris_flow: '土石流潛勢溪流', flood_650mm_24h: '650 毫米淹水範圍（24 小時）', geologic_sensitive_areas: '地質敏感區', nature_reserves: '自然保護區', highways: '高速公路', mrt_lines: '捷運路線', mrt_stations: '捷運車站', rail_lines: '鐵道路線', tra_stations: '臺鐵車站' };
  function translateLayerLabels() { if (savedLanguage !== 'zh-Hant') return; document.querySelectorAll('.layer-control label span').forEach(node => { const key = Object.keys(layerLabels).find(slug => node.textContent.toLowerCase().includes(slug)); if (key) node.textContent = layerLabels[key]; }); }
  setInterval(translateLayerLabels, 500);
  function trainTypeClass(name) { return String(name || '').replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '').slice(0, 8) || 'default'; }
  function headingFromPath(path, index) {
    const a = path[index], b = path[Math.min(path.length - 1, index + 1)];
    return Math.atan2(Number(b[1]) - Number(a[1]), Number(b[0]) - Number(a[0])) * 180 / Math.PI - 90;
  }
  function trainHeading(record) {
    const path = Array.isArray(record.track_path) ? record.track_path : [];
    if (path.length < 2) return null;
    const index = Math.min(path.length - 2, Math.max(0, Math.floor(Number(record.track_progress || 0) * (path.length - 1))));
    return headingFromPath(path, index);
  }
  function trainIcon(record) {
    const trainNo = record.train_no || '';
    const label = record.train_type_name || '臺鐵';
    const heading = trainHeading(record);
    const arrow = heading === null ? '' : '<i class="train-direction" aria-label="行駛方向" style="--train-heading:' + heading + 'deg"></i>';
    return L.divIcon({ className: 'nearby-icon train-icon train-' + trainTypeClass(label), html: '<span><b>' + trainNo + '</b><em>' + label + '</em></span>' + arrow, iconSize: [50, 50], iconAnchor: [25, 25] });
  }
  function interpolateTrainPath(path, progress) {
    if (!Array.isArray(path) || !path.length) return null;
    if (path.length === 1) return path[0];
    const scaled = Math.max(0, Math.min(1, Number(progress) || 0)) * (path.length - 1);
    const index = Math.min(path.length - 2, Math.floor(scaled));
    const fraction = scaled - index;
    const current = path[index], next = path[index + 1];
    return [
      Number(current[0]) + (Number(next[0]) - Number(current[0])) * fraction,
      Number(current[1]) + (Number(next[1]) - Number(current[1])) * fraction
    ];
  }
  function clearTrainLayer() {
    if (trainAnimationFrame) cancelAnimationFrame(trainAnimationFrame);
    trainAnimationFrame = null;
    trainAnimation.clear();
    trainMarkers.clear();
    trainGroup.clearLayers();
  }
  function runtimeStatusLabel(state) {
    return { available: '取得資料', disabled: '服務已停用', not_configured: '服務未設定',
      empty: '本次無資料', stale: '過期快取', unavailable: '服務暫不可用',
      error: '服務錯誤', unknown: '狀態未知' }[state] || '狀態未知';
  }
  function trainPositionLabel(item) {
    const labels = { upstream_coordinate: '上游回傳座標', track_live: '上游座標貼軌',
      track_estimate: '系統估算', estimate: '系統估算', station: '站點替代',
      station_route_unverified: '站點替代（路線未驗證）', route_unverified: '路線未驗證' };
    return (item.position_freshness === 'stale' ? '過期｜' : '') +
      (labels[item.position_source] || '位置來源未知');
  }
  function renderTrainMarkers(records) {
    const seen = new Set();
    trainAnimation.clear();
    records.filter(item => item.latitude != null && item.longitude != null && Number.isFinite(Number(item.latitude)) && Number.isFinite(Number(item.longitude))).forEach(item => {
      const key = String(item.train_no || item.station_id || Math.random());
      seen.add(key);
      const tooltip = document.createElement('div');
      const delay = Number(item.delay_minutes || 0);
      tooltip.append(textNode('b', (item.train_type_name || '臺鐵') + ' ' + (item.train_no || '')), textNode('div', '目前位置：' + (item.station_name || '未知')), textNode('div', trainPositionLabel(item)), textNode('div', '狀態：' + (delay > 0 ? '誤點 ' + delay + ' 分鐘' : '準點')), textNode('div', '目的地：' + (item.destination || '資料未提供')), textNode('small', item.updated_at ? new Date(item.updated_at).toLocaleString('zh-TW') : '更新時間未提供'));
      let marker = trainMarkers.get(key);
      if (marker) {
        marker.setIcon(trainIcon(item));
        marker.setTooltipContent(tooltip);
      } else {
        marker = L.marker([item.latitude, item.longitude], { icon: trainIcon(item) }).bindTooltip(tooltip, { direction: 'top', offset: [0, -18] }).addTo(trainGroup);
        trainMarkers.set(key, marker);
      }
      if (item.position_freshness !== 'stale' && Array.isArray(item.track_path) && item.track_path.length > 1) {
        const progress = Math.max(0, Math.min(1, Number(item.track_progress || 0)));
        marker.setLatLng(interpolateTrainPath(item.track_path, progress));
        if (progress < 1) trainAnimation.set(key, { marker, path: item.track_path, progress, startedAt: performance.now(), duration: Math.max(1, Number(item.segment_duration_seconds || 60)) });
      } else {
        marker.setLatLng([item.latitude, item.longitude]);
      }
    });
    trainMarkers.forEach((marker, key) => {
      if (!seen.has(key)) {
        trainGroup.removeLayer(marker);
        trainMarkers.delete(key);
      }
    });
    if (trainAnimationFrame) cancelAnimationFrame(trainAnimationFrame);
    animateTrainMarkers();
  }
  function animateTrainMarkers() {
    if (!trainAnimation.size) return;
    const now = performance.now();
    trainAnimation.forEach(state => {
      const progress = Math.min(1, state.progress + (now - state.startedAt) / 1000 / state.duration);
      const index = Math.min(state.path.length - 2, Math.floor(progress * (state.path.length - 1)));
      const point = interpolateTrainPath(state.path, progress);
      if (point) {
        state.marker.setLatLng(interpolateTrainPath(state.path, progress));
        const arrow = state.marker.getElement()?.querySelector('.train-direction');
        if (arrow && index < state.path.length - 1) arrow.style.setProperty('--train-heading', headingFromPath(state.path, index) + 'deg');
      }
    });
    trainAnimationFrame = requestAnimationFrame(animateTrainMarkers);
  }
  async function updateTrainLayer() {
    const status = $('live-train-status');
    let data;
    try {
      data = await api('/api/projects/' + currentSlug() + '/train-status/?limit=200');
    } catch (error) {
      console.error('train-layer', 'fetch', error);
      clearTrainLayer();
      status.textContent = '列車服務暫時無法使用';
      return;
    }
    try {
      renderTrainMarkers(data.records || []);
    } catch (error) {
      console.error('train-layer', 'render', error);
      status.textContent = '列車圖標繪製失敗：' + (error.message || '請重新整理頁面');
      return;
    }
    const plotted = (data.records || []).filter(item => item.latitude != null && item.longitude != null).length;
    status.textContent = runtimeStatusLabel(data.runtime_state) + '｜' + plotted + ' 班列車';
  }
  const esriSatelliteLabel = 'Esri Satellite Imagery';
  translations['zh-Hant']['intel.realtime'] = '即時資料';
  const trainToggle = document.querySelector('[data-live="train"]');
  trainToggle?.addEventListener('change', async () => { const status = $('live-train-status'); if (!trainToggle.checked) { if (trainRefreshTimer) clearInterval(trainRefreshTimer); trainRefreshTimer = null; clearTrainLayer(); status.textContent = '待命'; return; } status.textContent = '載入中'; await updateTrainLayer(); if (trainToggle.checked) trainRefreshTimer = setInterval(updateTrainLayer, 60000); });
  function editableLayers() { return map.pm.getGeomanLayers().filter(layer => !(layer instanceof L.Marker) && ![searchCircle, ...Object.values(overlayLayers)].includes(layer) && !publishedGeologyArea.hasLayer(layer) && !geologyBufferArea.hasLayer(layer)); }
  function collectDraftGeometry() { const category = $('feature-category').value; return { type: 'FeatureCollection', features: editableLayers().map(layer => { const feature = layer.toGeoJSON(); feature.properties = { ...(feature.properties || {}), category, source: 'map_drawing_tool' }; return feature; }).filter(f => f.geometry && f.geometry.type !== 'Point' && f.geometry.type !== 'MultiPoint') }; }
  function snapshot() { if (restoring) return; history.push(JSON.stringify(collectDraftGeometry())); if (history.length > 50) history.shift(); future = []; updateSummary(); }
  function restore(raw) { restoring = true; editableLayers().forEach(layer => layer.remove()); L.geoJSON(JSON.parse(raw), { filter: f => Boolean(f.geometry && f.geometry.type !== 'Point' && f.geometry.type !== 'MultiPoint'), onEachFeature: (_, layer) => layer.addTo(map) }); restoring = false; updateSummary(); }
  map.on('pm:create pm:edit pm:remove pm:dragend', snapshot);
  function openWorksiteEditor() { $('worksite-flow').hidden = true; $('worksite-toggle').setAttribute('aria-expanded', 'false'); $('plan-drawing-tools').hidden = false; project.setup_status = 'editing'; setSetup('編輯中', '請在地圖上繪製並儲存工區範圍。'); setSteps('edit'); map.pm.addControls({ position: 'topleft', drawMarker: false, drawCircle: false, drawCircleMarker: false, cutPolygon: false, rotateMode: false }); map.pm.setGlobalOptions({ snappable: true, snapDistance: 18 }); }
  on('start-worksite-edit', 'click', openWorksiteEditor);
  on('create-first-worksite', 'click', openWorksiteEditor);
  on('edit-worksite', 'click', openWorksiteEditor);
  document.querySelectorAll('[data-draw-mode]').forEach(button => button.addEventListener('click', () => map.pm.enableDraw({ point: 'Marker', polyline: 'Line', polygon: 'Polygon', rectangle: 'Rectangle' }[button.dataset.drawMode])));
  $('undo-plan-draft').addEventListener('click', () => { if (history.length < 2) return; future.push(history.pop()); restore(history[history.length - 1]); }); $('redo-plan-draft').addEventListener('click', () => { if (!future.length) return; const raw = future.pop(); history.push(raw); restore(raw); }); $('clear-plan-draft').addEventListener('click', () => { editableLayers().forEach(layer => layer.remove()); snapshot(); });
  $('measure-distance').addEventListener('click', () => { $('feature-category').value = '註記'; map.pm.enableDraw('Line', { snappable: true }); $('plan-drawing-status').textContent = '繪製折線後，儲存時由後端計算公尺長度。'; }); $('measure-area').addEventListener('click', () => { map.pm.enableDraw('Polygon', { snappable: true }); $('plan-drawing-status').textContent = '繪製面後，儲存時由後端計算平方公尺面積。'; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') map.pm.disableDraw(); if (event.ctrlKey && event.key.toLowerCase() === 'z') { event.preventDefault(); $('undo-plan-draft').click(); } if (event.ctrlKey && event.key.toLowerCase() === 'y') { event.preventDefault(); $('redo-plan-draft').click(); } if (event.key === 'Delete') map.pm.enableGlobalRemovalMode(); });

  async function loadPlans() { const data = await api('/api/projects/' + currentSlug() + '/plans/'); const list = $('plan-list'); list.replaceChildren(); if (!data.plans.length) { selectedPlan = null; planGroup.clearLayers(); list.appendChild(textNode('p', '尚未建立工區。', 'muted')); setWorksiteUiState('empty'); return; } data.plans.forEach(plan => { const button = textNode('button', plan.name + '｜' + statusLabel(plan.status) + ' v' + plan.version, 'plan-choice'); button.type = 'button'; button.addEventListener('click', () => selectPlan(plan)); list.appendChild(button); }); selectPlan(data.plans[0]); setWorksiteUiState('ready', data.plans[0]); }
  function statusLabel(status) { return { draft: '草稿', review: '送審', approved: '已核定', rejected: '退回' }[status] || status; }
  function selectPlan(plan) { selectedPlan = plan; setWorksiteUiState('ready', plan); planGroup.clearLayers(); L.geoJSON(plan.geometry, { filter: f => Boolean(f.geometry && f.geometry.type !== 'Point' && f.geometry.type !== 'MultiPoint'), style: { color: ['published', 'approved'].includes(plan.status) ? '#4f8b62' : '#d9c5b2', weight: ['published', 'approved'].includes(plan.status) ? 4 : 2, dashArray: ['published', 'approved'].includes(plan.status) ? null : '7 5', fillOpacity: .12 } }).addTo(planGroup); $('summary-version').textContent = 'v' + plan.version; $('summary-status').textContent = statusLabel(plan.status); $('summary-publication').textContent = plan.published_snapshot ? '發布 v' + plan.published_snapshot.version + (plan.published_snapshot.status === 'legacy_unverified' ? '（舊發布未驗證）' : '') : '尚未發布'; const validFeatures = (plan.geometry?.features || []).filter(f => f.geometry && f.geometry.type !== 'Point' && f.geometry.type !== 'MultiPoint'); $('summary-features').textContent = validFeatures.length; $('summary-updated').textContent = new Date(plan.updated_at).toLocaleString('zh-TW'); $('summary-area').textContent = sumProperty(plan, 'area_m2').toLocaleString() + ' m²'; $('summary-length').textContent = sumProperty(plan, 'length_m').toLocaleString() + ' m'; }
  function sumProperty(plan, key) { return (plan.geometry.features || []).reduce((sum, item) => sum + Number(item.properties?.[key] || 0), 0); }
  function updateSummary() { $('summary-features').textContent = editableLayers().length; }
  $('save-plan-draft').addEventListener('click', async () => { const geometry = collectDraftGeometry(); if (!geometry.features.length) { $('plan-drawing-status').textContent = '請先建立至少一個圖形。'; return; } const payload = { name: $('plan-draft-name').value, geometry, change_summary: $('change-summary').value, metadata: { category: $('feature-category').value } }; const url = selectedPlan ? '/api/projects/' + currentSlug() + '/plans/' + selectedPlan.id + '/' : '/api/projects/' + currentSlug() + '/plans/'; try { const data = await api(url, { method: selectedPlan ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), ...(selectedPlan ? {'X-WORKSITE-Lock-Version': String(selectedPlan.lock_version)} : {}) }, body: JSON.stringify(payload) }); selectedPlan = data.plan; $('plan-drawing-status').textContent = '已保存 v' + data.plan.version + '｜' + statusLabel(data.plan.status); setSetup('已保存', '已建立工區範圍，可以查看工程地質初步檢核。'); await loadPlans(); selectPlan(data.plan); setWorksiteUiState('ready', data.plan); scheduleGeologyRefresh(); } catch (error) { $('plan-drawing-status').textContent = '儲存失敗：' + error.message; } });
  $('import-plan-geojson').addEventListener('click', () => $('plan-geojson-file').click()); $('plan-geojson-file').addEventListener('change', event => { const file = event.target.files[0]; if (!file) return; file.text().then(raw => { const data = JSON.parse(raw); if (data.type !== 'FeatureCollection') throw new Error(); L.geoJSON(data, { onEachFeature: (_, layer) => layer.addTo(map) }); snapshot(); }).catch(() => { $('plan-drawing-status').textContent = 'GeoJSON 格式不正確。'; }); }); $('export-plan-geojson').addEventListener('click', () => { const url = URL.createObjectURL(new Blob([JSON.stringify(collectDraftGeometry())], { type: 'application/geo+json' })); const link = document.createElement('a'); link.href = url; link.download = currentSlug() + '-worksite.geojson'; link.click(); URL.revokeObjectURL(url); });

  document.querySelectorAll('[data-live]').forEach(check => check.addEventListener('change', async () => {
    const kind = check.dataset.live;
    if (['train', 'radar', 'dynamic-flood'].includes(kind)) return;
    const status = $('live-' + kind + '-status');
    if (kind !== 'traffic') {
      status.textContent = check.checked && Object.values(activeLayerItems).some(item => item.slug.includes(kind))
        ? '已疊加' : check.checked ? '此區域尚無資料' : '待命';
      return;
    }
    if (!check.checked) {
      stopHighwayTraffic();
      status.textContent = '待命';
      return;
    }
    status.textContent = '載入中';
    await loadHighwayTraffic();
    if (check.checked && !trafficTimer) trafficTimer = setInterval(loadHighwayTraffic, 60000);
  }));

  async function refreshAlerts() {
    try {
      const [current, historyData] = await Promise.all([api('/api/alerts/current/'), api('/api/alerts/history/?limit=50')]);
      const sourceStatus = {disabled: '未設定 CWA 來源', stale: 'CWA 過期快取', unknown: 'CWA 來源未確認', ok: 'CWA 最近同步'}[current.status] || 'CWA 暫時無法使用';
      if ($('alerts-status')) $('alerts-status').textContent = sourceStatus;
      const currentBox = $('current-alerts');
      currentBox.replaceChildren();
      if (!current.alerts.length) {
        currentBox.appendChild(textNode('p', current.status === 'ok' ? '目前無有效警報' : '目前無法確認 CWA 警報', 'muted'));
      }
      current.alerts.forEach(alert => {
        const confirmed = String(alert.source).toUpperCase() !== 'CWA' || current.status === 'ok';
        const cacheLabel = confirmed ? '' : '｜過期快取';
        currentBox.appendChild(textNode('div', '[' + alert.severity + '] ' + alert.title + cacheLabel, 'data-item'));
        if (!confirmed || alert.severity !== 'red') return;
        $('alert-overlay-title').textContent = alert.title;
        $('alert-overlay-summary').textContent = alert.payload?.summary || alert.source;
        $('alert-overlay').hidden = false;
        clearTimeout(alertTimer);
        const flood = String(alert.payload?.type || alert.source).toLowerCase().includes('flood');
        const duration = flood ? 60000 : 30000;
        const elapsed = Date.now() - new Date(alert.occurred_at).getTime();
        alertTimer = setTimeout(() => { $('alert-overlay').hidden = true; }, Math.max(0, duration - elapsed));
      });
      const historyBox = $('alert-history-list');
      historyBox.replaceChildren();
      historyData.alerts.forEach(alert => historyBox.appendChild(textNode('p', alert.title + '｜起報時間 ' + cwaTime(alert.occurred_at))));
    } catch (_) {
      if ($('alerts-status')) $('alerts-status').textContent = '服務暫時無法使用';
    }
  }
  refreshAlerts(); setInterval(refreshAlerts, 60000); on('alert-overlay-close', 'click', () => { $('alert-overlay')?.setAttribute('hidden', ''); });

  function modal(id, open) { const node = $(id); if (!node) return; node.hidden = !open; if (open) node.querySelector('button,select,input')?.focus(); if (id === 'settings-modal') $('settings-button')?.setAttribute('aria-expanded', String(open)); }
  on('settings-button', 'click', () => modal('settings-modal', true)); onSelector('[data-open-settings]', 'click', () => modal('settings-modal', true)); on('settings-close', 'click', () => modal('settings-modal', false)); on('settings-modal', 'click', event => { if (event.target === $('settings-modal')) modal('settings-modal', false); }); onSelector('[name=theme]', 'change', event => { document.documentElement.dataset.theme = event.target.value; document.body.dataset.theme = event.target.value; localStorage.setItem('worksite-theme', event.target.value); }); on('language-setting', 'change', event => localStorage.setItem('worksite-language', event.target.value)); const savedTheme = localStorage.getItem('worksite-theme') || 'dark'; document.body.dataset.theme = savedTheme; const savedThemeRadio = document.querySelector('[name=theme][value=' + savedTheme + ']'); if (savedThemeRadio) savedThemeRadio.checked = true;
  on('new-project-button', 'click', () => modal('new-project-modal', true)); onSelector('[data-close-project]', 'click', () => modal('new-project-modal', false)); on('new-project-form', 'submit', async event => { event.preventDefault(); const form = new FormData(event.target); try { const created = await api('/api/projects/', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(Object.fromEntries(form)) }); modal('new-project-modal', false); await loadProjects(created.slug); } catch (error) { event.target.appendChild(textNode('p', error.message, 'muted')); } });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { modal('settings-modal', false); modal('new-project-modal', false); } });

  Promise.all([loadProjects(), loadLayers()]).then(() => { createGeologyControls(); document.querySelectorAll('.layer-control').forEach(node => { const label = node.querySelector('label span')?.textContent || ''; if (label === 'geologic_sensitive_areas' || label === '地質敏感區') node.remove(); }); createGeologicSensitiveControl(); history = [JSON.stringify({ type: 'FeatureCollection', features: [] })]; setTimeout(() => { $('cctv-layer')?.querySelectorAll('small').forEach(node => node.remove()); const parent = $('weather-layer')?.parentElement; if (parent) parent.insertBefore($('cctv-layer'), $('weather-layer')); if ($('cctv-layer') && $('cctv-viewer')) $('cctv-layer').appendChild($('cctv-viewer')); }, 0); }).catch(error => setSetup('載入失敗', error.message));
  function cwaTime(value) {
    const date = new Date(value);
    return value && Number.isFinite(date.getTime()) ? date.toLocaleString('zh-TW', {timeZone: 'Asia/Taipei'}) : '未知';
  }
  function cwaFailure(error) {
    const reason = error.payload?.reason || error.payload?.error;
    if (reason === 'disabled') return '服務已停用';
    if (reason === 'not_configured') return '未設定來源';
    if (reason === 'CWA credential missing') return '未設定來源';
    if (reason === 'upstream_timeout') return '來源逾時';
    return '來源暫時無法使用';
  }
  function weatherMetric(value, unit) { return value === null || value === undefined || value === '' ? '—' : value + ' ' + unit; }
  function weatherSummary(item) { return (item.distance_km ?? '—') + ' km｜雨量 ' + weatherMetric(item.precipitation, 'mm') + '｜風速 ' + weatherMetric(item.wind_speed, 'm/s'); }
  function focusNearbyCard(containerId, key) {
    const container = $(containerId);
    if (!container) return;
    const wanted = String(key ?? '');
    const card = Array.from(container.querySelectorAll('[data-nearby-id]')).find(node => node.dataset.nearbyId === wanted);
    (card || container).scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (card) {
      card.classList.add('is-focused');
      window.setTimeout(() => card.classList.remove('is-focused'), 1800);
    }
  }
  function focusCctvField() {
    ensureCctvWidgets();
    const target = $('cctv-viewer') || $('cctv-layer');
    if (!target) return;
    target.hidden = false;
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.add('is-focused');
    window.setTimeout(() => target.classList.remove('is-focused'), 1800);
  }
  function renderWeather(container, title, records, group) {
    container.replaceChildren(textNode('b', '☁ 氣象站')); group.clearLayers();
    container.appendChild(textNode('p', title, 'muted'));
    if (!records.length) return;
    records.slice(0, 8).forEach(item => {
      const name = item.name || item.external_id || '氣象站';
      const nearbyId = String(item.external_id || item.station_id || name);
      const card = textNode('div', '', 'data-item');
      card.dataset.nearbyId = nearbyId;
      card.append(textNode('b', name), textNode('small', weatherSummary(item)), textNode('small', '觀測 ' + cwaTime(item.observed_at)));
      container.appendChild(card);
      if (item.latitude && item.longitude) {
        const tooltipText = name + '\n' + weatherSummary(item);
        const marker = L.marker([item.latitude, item.longitude], { icon: markerIcon('☁', 'weather') })
          .bindTooltip(tooltipText, { direction: 'top', offset: [0, -18], className: 'weather-tooltip' })
          .addTo(group);
        marker.on('click', () => focusNearbyCard('weather-layer', nearbyId));
      }
    });
  }
  const genericRenderData = renderData;
  renderData = function (container, title, records, icon, group, iconClass) {
    return iconClass === 'weather' ? renderWeather(container, title, records, group) : genericRenderData(container, title, records, icon, group, iconClass);
  };
  function cctvSummary(item) { return (item.distance_km ?? '—') + ' km｜' + (item.city || '—') + '｜' + (item.stream_url ? '影像來源待確認' : '未提供影像來源'); }
  function renderCctv(records, errorMsg, state) {
    ensureCctvWidgets(); cctvGroup.clearLayers();
    const layer = $('cctv-layer');
    if (!layer) return;
    layer.replaceChildren(textNode('b', '▣ CCTV' + (state ? '｜' + state : '')));
    if (errorMsg) { layer.appendChild(textNode('p', errorMsg, 'muted')); return; }
    if (!records.length) {
      layer.appendChild(textNode('p', state && state !== runtimeStatusLabel('empty')
        ? '沒有可確認的攝影機資料' : '2 km 內無 CCTV 資料', 'muted'));
      return;
    }
    records.slice(0, 8).forEach(item => {
      const name = item.name || item.location_name || item.cctv_id || 'CCTV';
      if (item.latitude && item.longitude) {
        const tooltipText = name + '\n' + cctvSummary(item);
        const marker = L.marker([item.latitude, item.longitude], { icon: markerIcon('▣', 'cctv') })
          .bindTooltip(tooltipText, { direction: 'top', offset: [0, -18], className: 'cctv-tooltip' })
          .addTo(cctvGroup);
        marker.on('click', () => {
          openCctv(item);
          focusCctvField();
        });
      }
    });
  }
  const cctvViewerNode = $('cctv-viewer');
  function restoreCctvWidgets() {
    const layer = $('cctv-layer');
    if (!layer) return;
    if (cctvViewerNode && cctvViewerNode.parentElement !== layer) layer.appendChild(cctvViewerNode);
  }
  function openCctvHls(item, viewer, player, snapshot, status) {
    const url = item.stream_url;
    viewer.hidden = false;
    $('cctv-viewer-title').textContent = item.name || item.cctv_id || 'CCTV 影像';
    player.hidden = true;
    snapshot.hidden = true;
    player.removeAttribute('src');
    snapshot.removeAttribute('src');
    status.textContent = '載入現場影像中…';

    if (player._worksiteHls) {
      player._worksiteHls.destroy();
      player._worksiteHls = null;
    }

    if (player.canPlayType('application/vnd.apple.mpegurl')) {
      player.src = url;
      player.hidden = false;
      player.onplaying = () => { status.textContent = '現場影像播放中'; };
      player.onerror = () => { status.textContent = 'HLS 影像無法載入，請開啟原始來源'; };
      return;
    }

    if (window.Hls && Hls.isSupported()) {
      const hls = new Hls();
      player._worksiteHls = hls;
      hls.loadSource(url);
      hls.attachMedia(player);
      player.onplaying = () => { status.textContent = '現場影像播放中'; };
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        player.hidden = false;
        player.play().catch(() => { status.textContent = '影像尚未播放，請按播放'; });
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data && data.fatal) status.textContent = 'HLS 影像無法載入，請開啟原始來源';
      });
      return;
    }

    status.textContent = '此瀏覽器不支援 HLS，請開啟原始來源';
  }
  function stopPreviousCctv(player, snapshot) {
    if (player?._worksiteHls) {
      player._worksiteHls.destroy();
      player._worksiteHls = null;
    }
    player?.pause();
    player?.removeAttribute('src');
    snapshot?.removeAttribute('src');
    const frame = document.getElementById('cctv-frame');
    if (frame) {
      frame.hidden = true;
      frame.removeAttribute('src');
    }
  }
  const originalOpenCctv = openCctv;
  openCctv = function (item) {
    restoreCctvWidgets();
    const viewer = $('cctv-viewer');
    const player = $('cctv-player');
    const snapshot = $('cctv-snapshot');
    const status = $('cctv-viewer-status');
    if (!viewer || !player || !snapshot || !status) return;
    stopPreviousCctv(player, snapshot);
    const type = String(item.media_type || '').toLowerCase();
    if (/\.m3u8(?:\?|$)/i.test(item.stream_url || '') || type.includes('hls')) {
      return openCctvHls(item, viewer, player, snapshot, status);
    }
    return originalOpenCctv(item);
  };
  $('cctv-viewer-close')?.addEventListener('click', () => {
    const player = $('cctv-player');
    if (player && player._worksiteHls) {
      player._worksiteHls.destroy();
      player._worksiteHls = null;
    }
  });
}());
