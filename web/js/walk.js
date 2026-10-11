import {h,api,fmt,polaroid,say} from './ui.js';
const HAB=['park','garden','campus','trail','backyard'],sleep=ms=>new Promise(r=>setTimeout(r,ms));
export async function walkPage(el,id){
let {walk,discoveries:ds}=await api(`/api/walks/${id}`);
const [hl,fr,qs]=await Promise.all([api('/api/health').catch(()=>null),api(`/api/walks/${id}/firsts`).catch(()=>({firsts:[]})),api(`/api/walks/${id}/quests`).catch(()=>null)]);
const first=new Map(fr.firsts.map(f=>[f.discovery_id,f.category])),mode=new Set(),err=new Map(),busy=new Set(),nodes=new Map();
let done=qs?qs.done:[],ideas=qs?qs.suggestions:null;
const grid=h('div',{class:'grid2'}),status=h('p',{class:'quiet',role:'status'}),sum=h('p',{class:'quiet',role:'status'}),strip=h('div',{class:'ideas'});
const upd=d=>{ds=ds.map(x=>x.id===d.id?d:x);mode.delete(d.id);err.delete(d.id);redraw(d.id)};
function redraw(i){const d=ds.find(x=>x.id===i),old=nodes.get(i),n=card(d);nodes.set(i,n);old&&old.replaceWith(n)}
async function identifyOne(d){for(let t=0;t<30;t++){try{return await api(`/api/discoveries/${d.id}/identify`,{method:'POST'})}catch(e){if(e.status!==409)throw e;await sleep(2500)}}throw new Error('Identification is still busy. Please try again in a moment.')}
async function act(d,fn){busy.add(d.id);err.delete(d.id);redraw(d.id);try{const nd=await fn();busy.delete(d.id);upd(nd);
if(nd.status==='confirmed'){const f=await api(`/api/walks/${id}/firsts`).catch(()=>null);if(f){const m=f.firsts.find(x=>x.discovery_id===nd.id);m?first.set(nd.id,m.category):first.delete(nd.id);redraw(nd.id)}}}catch(e){busy.delete(d.id);err.set(d.id,e.message);redraw(d.id)}}
const post=(d,p,json)=>()=>api(`/api/discoveries/${d.id}/${p}`,{method:'POST',json});
async function toJournal(d){try{const j=await api(`/api/walks/${id}/journal`);const free=j.polaroids.find(p=>!p.removed&&!p.discovery_id)||j.polaroids.find(p=>p.removed);
let slot=free&&free.slot;if(!slot){slot=await new Promise(res=>{const dlg=h('dialog',{class:'picker','aria-label':'Replace a photo'},h('p',{text:'Both Polaroids are in use. Which one should this replace?'}),h('div',{class:'acts2'},j.polaroids.map(p=>h('button',{class:'btn',text:`Polaroid ${p.slot}`,onclick:()=>{res(p.slot);dlg.close()}})),h('button',{class:'btn link',text:'Cancel',onclick:()=>dlg.close()})));dlg.onclose=()=>{res(null);dlg.remove()};document.body.append(dlg);dlg.showModal()})}
if(!slot)return;await api(`/api/walks/${id}/journal/polaroids/${slot}/photo`,{method:'PUT',json:{discovery_id:d.id}});say('Put on the journal page.')}catch(e){say(e.message)}}
function card(d){const B=(t,fn,c='')=>h('button',{class:'btn sm '+c,text:t,disabled:busy.has(d.id),onclick:fn});
const R=(fn)=>()=>act(d,fn),again=B('Try again',R(()=>identifyOne(d)));const body=h('div',{});let a=[];
const fact=d.ai_fact&&h('p',{class:'dyk'},h('em',{text:'Did you know? '}),d.ai_fact);
if(d.status==='confirmed'){body.append(h('h2',{text:d.final_label}),h('p',{class:'cat',text:d.final_category}),h('p',{},'✓ Confirmed'),first.has(d.id)&&h('p',{class:'quiet',text:`A first ${first.get(d.id)} for you.`}),fact);a=[B('Put on the journal page',()=>toJournal(d))]}
else if(mode.has(d.id)){const inp=h('input',{type:'text',maxlength:120,id:'c'+d.id,placeholder:'e.g. Painted lady',value:d.final_label||d.ai_identification||''});
const save=()=>inp.value.trim()?act(d,()=>api(`/api/discoveries/${d.id}/correct`,{method:'POST',json:{label:inp.value.trim()}})):say('Please type what it is.');inp.onkeydown=e=>e.key==='Enter'&&save();
body.append(h('h2',{text:d.suggestion_text||'Name it'}),h('label',{for:'c'+d.id,class:'quiet',text:'What is it really?'}),h('div',{class:'acts2'},inp,B('Save',save,'pri'),B('Cancel',()=>{mode.delete(d.id);redraw(d.id)},'link')));setTimeout(()=>inp.focus())}
else if(d.status==='saved_unidentified'||d.status==='rejected'){body.append(h('h2',{text:'Kept without a name.'}),h('p',{text:'You can name it later, or ask again whenever you like.'}));a=[again,B('Name it',()=>{mode.add(d.id);redraw(d.id)})]}
else if(d.ai_status===null){a=err.has(d.id)?[again]:[]}
else{const c=d.ai_confidence;body.append(h('h2',{text:d.suggestion_text}),h('p',{class:'cat',text:d.ai_status==='ok'?d.ai_category:'unknown'}),d.ai_explanation&&h('p',{text:d.ai_explanation}),fact);
const ok=B('Confirm',R(post(d,'confirm')),'pri'),cor=B('Correct',()=>{mode.add(d.id);redraw(d.id)}),no=B('Not this',R(post(d,'reject')),'link');
a=d.ai_status==='ok'&&c==='High'?[ok,cor,no]:d.ai_status==='ok'&&c==='Medium'?[ok,cor,again,no]:[again,B('Save anyway',R(post(d,'save'))),no]}
body.append(d.warning&&h('p',{class:'warn',text:d.warning}),err.has(d.id)&&h('p',{class:'quiet',text:err.get(d.id)}),a.length&&!mode.has(d.id)&&h('div',{class:'acts2'},a));
return h('article',{class:'card'},polaroid(d,d.original_name&&d.original_name.slice(0,14)),body)}
function drawGrid(){nodes.clear();grid.replaceChildren(...ds.map(d=>{const n=card(d);nodes.set(d.id,n);return n}));if(!ds.length)grid.replaceChildren(h('p',{class:'quiet',text:'No photos yet. A walk with no photos is still a walk.'}))}
function drawStrip(){if(!ideas){strip.replaceChildren();return}const ids=new Set(done.map(q=>q.template_id));
strip.replaceChildren(h('span',{class:'quiet',text:'Ideas for a walk, only if you like'}),
...done.map(q=>h('span',{class:'idea'},'✓ '+q.wording,h('button',{class:'btn link sm',text:'Undo','aria-label':'Undo '+q.wording,onclick:async()=>{try{await api(`/api/walks/${id}/quests/${q.template_id}/done`,{method:'DELETE'});done=done.filter(x=>x!==q);drawStrip()}catch(e){say(e.message)}}}))),
...ideas.filter(q=>!ids.has(q.id)).map(q=>h('span',{class:'idea'},q.text,h('button',{class:'btn sm',text:'I did this','aria-label':'I did this: '+q.text,onclick:async()=>{try{done=[...done,await api(`/api/walks/${id}/quests/${q.id}/done`,{method:'POST'})];drawStrip()}catch(e){say(e.message)}}}))))}
async function upload(files){files=[...files];if(!files.length)return;const f=new FormData();files.forEach(x=>f.append('files',x));files.forEach(x=>f.append('captured_ms',String(x.lastModified)));
sum.textContent='Adding photos…';try{const r=await api(`/api/walks/${id}/photos`,{method:'POST',form:f}),s=r.summary;
const bits=[`${s.added} photo${s.added===1?'':'s'} added.`];if(s.duplicates)bits.push(`${s.duplicates} already here, so skipped.`);if(s.errors.length)bits.push(`${s.errors.length} could not be read (${s.errors.map(e=>e[0]).join(', ')}).`);
sum.textContent=bits.join(' ');ds=(await api(`/api/walks/${id}`)).discoveries;drawGrid()}catch(e){sum.textContent=e.message}}
async function identifyAll(){const todo=ds.filter(d=>d.status==='imported');if(!todo.length){status.textContent='Nothing new to look at.';return}
status.textContent='Looking closely at your photos…';for(const d of todo){busy.add(d.id);redraw(d.id);try{upd(await identifyOne(d))}catch(e){err.set(d.id,e.message);redraw(d.id);if(e.status===503){status.textContent='';busy.delete(d.id);redraw(d.id);break}}busy.delete(d.id)}status.textContent=''}
const inp=h('input',{type:'file',accept:'image/*',multiple:true,hidden:true,onchange:e=>{upload(e.target.files);e.target.value=''}});
const patch=async b=>{try{walk=(await api(`/api/walks/${id}`,{method:'PATCH',json:b})).walk}catch(e){say(e.message)}};
const area=h('input',{type:'text',id:'area',maxlength:200,value:walk.area||'',onblur:()=>area.value.trim()!==(walk.area||'')&&patch({area:area.value.trim()||null})});
const dur=h('input',{type:'number',id:'dur',min:1,max:1440,value:walk.duration_min||'',style:'width:90px',onblur:()=>{const v=dur.value.trim();if(v===''){walk.duration_min&&patch({duration_min:null});return}const n=+v;if(!Number.isInteger(n)||n<1||n>1440){say('Please use a whole number of minutes from 1 to 1440.');dur.value=walk.duration_min||'';return}n!==walk.duration_min&&patch({duration_min:n})}});
const chips=HAB.map(x=>{const b=h('button',{class:'chip'+(walk.habitat===x?' on':''),'aria-pressed':String(walk.habitat===x),text:x[0].toUpperCase()+x.slice(1)});
b.onclick=async()=>{const nv=walk.habitat===x?null:x;await patch({habitat:nv});chips.forEach((c,i)=>{const on=HAB[i]===walk.habitat;c.classList.toggle('on',on);c.setAttribute('aria-pressed',String(on))})};return b});
const drop=h('div',{class:'drop',text:'Drop photos here, or use Add photos. Photos you already added are skipped.'});
['dragover','dragenter'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.add('over')}));['dragleave','drop'].forEach(t=>drop.addEventListener(t,()=>drop.classList.remove('over')));drop.addEventListener('drop',e=>{e.preventDefault();upload(e.dataTransfer.files)});
el.replaceChildren(h('section',{class:'sheet'},hl&&hl.ollama===false&&h('p',{class:'banner',text:'The local model isn’t running right now. Your photos are safe, and importing and the journal still work.'}),
h('div',{class:'head'},h('div',{},h('p',{class:'eyebrow',text:'Today’s walk'}),h('h1',{text:fmt(walk.date)})),h('div',{class:'acts'},h('button',{class:'btn',text:'Add photos',onclick:()=>inp.click()}),inp,h('button',{class:'btn pri',text:'Identify new photos',onclick:identifyAll}))),
h('div',{class:'det'},h('label',{for:'area',class:'quiet',text:'Where'}),area,h('span',{},chips),h('label',{for:'dur',class:'quiet',text:'For about'}),dur,h('span',{class:'quiet',text:'minutes'})),drop,sum,status,grid,strip));
drawGrid();drawStrip()}