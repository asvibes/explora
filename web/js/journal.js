import {h,api,fmt,pickPhoto,say,photo} from './ui.js';
export async function journalPage(el,id){
let j=await api(`/api/walks/${id}/journal`);const base=`/api/walks/${id}/journal`;
const [pw,nw]=await Promise.all([j.previous_walk_id&&api(`/api/walks/${j.previous_walk_id}`).catch(()=>null),j.next_walk_id&&api(`/api/walks/${j.next_walk_id}`).catch(()=>null)]);
const set=async(p,o)=>{try{j=await api(p,o);draw()}catch(e){say(e.message)}};
const fileBtn=(t,fn)=>{const i=h('input',{type:'file',accept:'image/*',hidden:true,onchange:e=>{const f=e.target.files[0];e.target.value='';f&&fn(f)}});return[h('button',{class:'btn sm',text:t,onclick:()=>i.click()}),i]};
const putPage=f=>{const fd=new FormData();fd.append('file',f);set(base+'/left-page',{method:'PUT',form:fd})};
function slot(p){
if(p.removed)return h('div',{class:'slot gone'},h('button',{class:'btn',text:'Bring back',onclick:()=>set(`${base}/polaroids/${p.slot}/restore`,{method:'POST'})}));
const d=p.discovery,cap=h('input',{class:'cap',type:'text',value:p.caption,placeholder:'Write a caption…','aria-label':`Caption for Polaroid ${p.slot}`});
const save=async()=>{const v=cap.value.trim();if(v===p.caption)return;if(v.length>200){say('That caption is too long (200 characters max). Please shorten it.');return}
try{j=await api(`${base}/polaroids/${p.slot}/caption`,{method:'PUT',json:{caption:v}});p.caption=v}catch(e){say(e.message)}};
cap.onblur=save;cap.onkeydown=e=>e.key==='Enter'&&cap.blur();
const open=b=>pickPhoto(id,p.discovery_id,'Choose a photo for this Polaroid',async did=>{j=await api(`${base}/polaroids/${p.slot}/photo`,{method:'PUT',json:{discovery_id:did}});draw()},b).catch(e=>say(e.message));
const pb=h('button',{class:'ph','aria-label':d?'Change photo':'Choose a photo'},d?h('img',{src:photo(d.id),alt:'',style:'width:100%;aspect-ratio:1;object-fit:cover;display:block'}):h('div',{class:'empty',text:'Choose a photo'}));pb.onclick=()=>open(pb);
return h('div',{class:'slot'},h('div',{class:'pol',style:`--t:${p.tilt||0}deg;padding-bottom:10px`},h('div',{class:'tape'}),
h('button',{class:'x','aria-label':`Remove Polaroid ${p.slot}`,onclick:()=>set(`${base}/polaroids/${p.slot}`,{method:'DELETE'})},h('span',{text:'×'})),pb,cap))}
const nav=(w,cls,txt)=>w&&h('button',{class:'btn sm nav '+cls,text:txt(fmt(w.walk.date,false,true)),onclick:()=>location.hash='#/journal/'+w.walk.id});
const book=h('div',{class:'book'}),under=h('div',{class:'under'});
function draw(){const lp=j.left_page,odd=lp.rotation%180!==0;
const left=h('section',{class:'pg left','aria-label':'Your handwritten page'},
lp.url?[h('div',{class:'opts'},h('span',{class:'lbl',text:'••• Page options'}),h('div',{class:'menu'},h('button',{class:'btn sm',text:'Rotate',onclick:()=>set(base+'/left-page/rotate',{method:'POST',json:{degrees:90}})}),fileBtn('Replace',putPage),h('button',{class:'btn sm',text:'Remove',onclick:()=>set(base+'/left-page',{method:'DELETE'})}))),
h('img',{class:'scan',src:lp.url,alt:'Photo of your handwritten page',style:`transform:rotate(${lp.rotation}deg)${odd?' scale(.72)':''}`})]
:h('div',{class:'inv'},h('p',{text:'Your own page goes here, if you have one.'}),fileBtn('Add a photo of your page',putPage)),
nav(pw,'l',t=>'‹ '+t));
const right=h('section',{class:'pg right','aria-label':'Photos'},j.polaroids.map(slot),nav(nw,'r',t=>t+' ›'));
book.replaceChildren(left,right);
under.replaceChildren(h('h2',{text:fmt(j.walk.date,true)}),h('p',{style:'margin:4px 0;font-size:14px'},j.walk.area?j.walk.area+' · ':'',h('a',{href:'#/walk/'+id,text:'See this walk’s photos'})))}
el.replaceChildren(book,under);draw()}