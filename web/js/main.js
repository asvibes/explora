import {h,api,fmt,photo,say} from './ui.js';import {home} from './dashboard.js';import {walkPage} from './walk.js';import {journalPage} from './journal.js';
const view=document.getElementById('view'),root=document.documentElement,tb=document.getElementById('theme');
const paint=t=>{root.dataset.theme=t;tb.textContent=t==='dark'?'☀':'☾'};paint('light');
api('/api/settings').then(s=>paint(s.theme==='dark'?'dark':'light')).catch(()=>{});
tb.onclick=async()=>{const t=root.dataset.theme==='dark'?'light':'dark';paint(t);try{await api('/api/settings',{method:'PUT',json:{theme:t}})}catch(e){say(e.message)}};
const today=async()=>(await api('/api/walks/today',{method:'POST'})).walk.id;
async function collection(el){const c=await api('/api/collection');el.replaceChildren(h('section',{class:'sheet'},h('h1',{text:'Collection'}),c.total?h('div',{},h('p',{text:`${c.total} discoveries so far.`}),h('ul',{},Object.entries(c.by_category).map(([k,n])=>h('li',{text:`${n} ${k}`})))):h('p',{class:'quiet',text:'Nothing here yet. That is perfectly fine.'})))}
async function firsts(el){const {firsts:f}=await api('/api/firsts');el.replaceChildren(h('section',{class:'sheet'},h('h1',{text:'Firsts'}),f.length?h('div',{class:'list',style:'margin-top:24px'},f.map(x=>h('a',{class:'fcard',href:'#/journal/'+x.walk_id,style:'text-decoration:none'},x.discovery_id&&h('div',{class:'pol'},h('img',{src:photo(x.discovery_id),alt:''})),h('p',{},x.text,h('br'),h('span',{class:'quiet',text:fmt(x.walk_date,true)}))))):h('p',{class:'quiet',text:'Your first of each kind will be kept here.'})))}
async function route(){const [,p='home',arg]=(location.hash||'#/home').split('/');
document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.p===p));
view.classList.remove('in');void view.offsetWidth;view.classList.add('in');
try{if(p==='walk')await walkPage(view,arg||await today());else if(p==='journal')await journalPage(view,arg||await today());
else if(p==='collection')await collection(view);else if(p==='firsts')await firsts(view);else await home(view)}
catch(e){view.replaceChildren(h('section',{class:'sheet'},h('p',{class:'quiet',text:e.message})))}}
addEventListener('hashchange',route);route();