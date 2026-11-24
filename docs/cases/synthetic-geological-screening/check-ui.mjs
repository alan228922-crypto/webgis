import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
const js = fs.readFileSync('home/static/home/js/map.js','utf8')
const constants = 'const geologyLayerControls={}; const geologyGenerations=new Map(); let displayedGeologySource=null;'
const start = js.indexOf('  function acceptGeologySource(')
const end = js.indexOf('  function geologyRequestKey(',start)
const source={textContent:''}
const context=vm.createContext({location: {origin:'http://localhost'}, currentSlug:()=>'worksite',
  $: id=>id==='geology-worksite-source'?source:null, scheduleGeologyRefresh:()=>{},
  publishedGeologyArea:{clearLayers(){}},geologyBufferArea:{clearLayers(){}}})
vm.runInContext(constants+js.slice(start,end),context)
assert.equal(vm.runInContext("acceptGeologySource('worksite',{worksiteGeneration:2,worksiteReference:'verified:7:2:hash'})",context),true)
assert.match(source.textContent,/v2/)
assert.equal(vm.runInContext("acceptGeologySource('worksite',{worksiteGeneration:1,worksiteReference:'verified:7:1:old'})",context),false)
assert.match(source.textContent,/v2/)
assert.equal(vm.runInContext("acceptGeologySource('worksite',{worksiteGeneration:3,worksiteReason:'source_archived'})",context),true)
assert.match(source.textContent,/封存/)
assert.match(js,/mode: 'screening', buffer_m: String\(screeningRadius\)/)
assert.match(js,/mode=exploration&bbox=/)
assert.match(js,/moveend[^\n]+scheduleGeologyRefresh\(false\)/)
assert.match(js,/工區重疊[^\n]+無法計算/)
console.log('Frontend verified: distinct modes, moveend exploration only, stale source rejected, archive reason, null-area display.')
