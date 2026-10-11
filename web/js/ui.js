export const h=(t,a={},...k)=>{const e=document.createElement(t);for(const[x,v]of Object.entries(a)){if(v==null||v===false)continue;
if(x.startsWith('on'))e.addEventListener(x.slice(2),v);else if(x==='class')e.className=v;else if(x==='text')e.textContent=v;else e.setAttribute(x,v===true?'':v)}
k.flat(9).forEach(c=>c!=null&&c!==false&&e.append(c.nodeType?c:document.createTextNode(c)));return e};
export async function api(path,o={}){const init={method:o.method||'GET',headers:{}};
if(o.json!==undefined){init.headers['Content-Type']='application/json';init.body=JSON.stringify(o.json)}if(o.form)init.body=o.form;
let r;try{r=await fetch(path,init)}catch{throw Object.assign(new Error("Can't reach Explora on this laptop. Is the server running?"),{status:0})}
let d=null;try{d=await r.json()}catch{}
if(!r.ok){let m=d&&d.detail;if(Array.isArray(m))m='That value is not allowed.';throw Object.assign(new Error(m||`Something went wrong (${r.status}).`),{status:r.status})}return d}
export const photo=id=>`/api/discoveries/${id}/photo`;
export const tilt=id=>((id*37%61)/60*2-1)*2.5;   // stable, never stored
const MO=['January','February','March','April','May','June','July','August','September','October','November','December'];
const WD=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
export const MONTHS=MO;
export function fmt(iso,year,short){const d=new Date(iso+'T00:00:00');
if(short)return `${WD[d.getDay()].slice(0,3)} ${d.getDate()} ${MO[d.getMonth()].slice(0,3)}`;
return `${WD[d.getDay()]}, ${d.getDate()} ${MO[d.getMonth()]}${year?' '+d.getFullYear():''}`}
let tt;export function say(m){const t=document.getElementById('toast');t.textContent=m;t.className='on';clearTimeout(tt);tt=setTimeout(()=>t.className='',5000)}
export function polaroid(d,label){return h('div',{class:'pol',style:`--t:${tilt(d.id)}deg`},h('img',{src:photo(d.id),alt:'',loading:'lazy'}),label&&h('span',{class:'fn',text:label}))}
export async function pickPhoto(walkId,currentId,title,onPick,opener){
const {discoveries}=await api(`/api/walks/${walkId}`);
const dlg=h('dialog',{class:'picker','aria-label':title});
dlg.append(h('div',{class:'tape'}),h('p',{class:'eyebrow',text:title}),
discoveries.length?h('div',{class:'g'},discoveries.map(d=>h('button',{class:'pol'+(d.id===currentId?' sel':''),'aria-label':d.original_name||'Photo',onclick:async()=>{try{await onPick(d.id);dlg.close()}catch(e){say(e.message)}}},h('img',{src:photo(d.id),alt:''})))):h('p',{class:'quiet',text:'No photos in this walk yet.'}),
h('button',{class:'btn',text:'Close',onclick:()=>dlg.close()}));
dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close()});
dlg.addEventListener('close',()=>{dlg.remove();opener&&opener.isConnected&&opener.focus()});
document.body.append(dlg);dlg.showModal()}