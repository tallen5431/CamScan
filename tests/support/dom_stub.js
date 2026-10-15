// Minimal browser stand-in so the real assets/*.js modules can be exercised headlessly
// in node. Not a DOM implementation — just enough surface for a module to load and for
// its pure logic to be driven, so client behaviour is covered by tests even though the
// browser e2e suite (playwright) is not available in every environment.
const noop = () => {};
const mkEl = () => ({ style:{cssText:''}, classList:{toggle:noop,add:noop,remove:noop,contains:()=>false},
  appendChild:noop, append:noop, remove:noop, setAttribute:noop, removeAttribute:noop,
  getAttribute:()=>null, addEventListener:noop, querySelector:()=>null, querySelectorAll:()=>[],
  focus:noop, click:noop, insertBefore:noop, set innerHTML(v){}, get innerHTML(){return '';},
  set textContent(v){}, get textContent(){return '';}, files:[], value:'' });
global.window = { addEventListener:noop, parent:null, location:{search:''} };
global.document = { readyState:'complete', createElement:mkEl, body:mkEl(),
  documentElement:mkEl(), getElementById:()=>null, querySelector:()=>null,
  querySelectorAll:()=>[], addEventListener:noop, dispatchEvent:noop, referrer:'' };
global.localStorage = { _d:{}, getItem(k){return this._d[k]??null;}, setItem(k,v){this._d[k]=String(v);}, removeItem(k){delete this._d[k];} };
global.MutationObserver = class { observe(){} disconnect(){} };
global.CustomEvent = class { constructor(t,o){ this.type=t; Object.assign(this,o);} };
global.navigator = { userAgent:'test' };
global.fetch = () => Promise.reject(new Error('no network in test'));
global.location = { search:'' };
