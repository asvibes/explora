import {h,api,photo,tilt,fmt,MONTHS,pickPhoto,say} from './ui.js';
const LINES=['Moss holds on to water long after the rain has gone.','Most leaves are greener on top and paler underneath.','A bird you hear is often easier to find than one you look for.','Clouds are worth a second look.','Every stone has been somewhere else.','Bees pay attention to colour before shape.','Shadows are longest when the light is low.'];
const pad=n=>String(n).padStart(2,'0');const isoOf=d=>`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
async function openToday(kind){try{const v=await api('/api/walks/today',{method:'POST'});location.hash=`#/${kind}/${v.walk.id}`}catch(e){say(e.message)}}
function collectionLine(c){if(!c.total)return h('p',{class:'quiet',text:'Nothing here yet. That is perfectly fine.'});
const parts=Object.entries(c.by_category).map(([k,n])=>`${n} ${k}${n>1&&!k.endsWith('s')?'s':''}`);
return h('p',{},h('b',{text:`${c.total} discoveries`}),` so far: ${parts.join(', ')}.`)}
export async function home(el){
const now=new Date(),todayIso=isoOf(now);let y=now.getFullYear(),m=now.getMonth();
const [s,col]=await Promise.all([api('/api/settings'),api('/api/collection')]);
const hr=now.getHours(),greet=hr<12?'Good morning':hr<18?'Good afternoon':'Good evening',nm=s.preferred_name||s.name;
const calEl=h('section',{class:'cal','aria-label':'Walk calendar'});
async function load(){
const mm=`${y}-${pad(m+1)}`;let walks=[];try{walks=(await api(`/api/calendar/${mm}`)).walks}catch(e){say(e.message)}
const by=Object.fromEntries(walks.map(w=>[w.date,w]));const first=new Date(y,m,1).getDay(),n=new Date(y,m+1,0).getDate();
const cells=[...Array(first)].map(()=>h('div',{class:'day'}));
for(let d=1;d<=n;d++){const iso=`${y}-${pad(m+1)}-${pad(d)}`,w=by[iso];
const c=h('div',{class:'day'+(iso>todayIso?' future':'')},h('span',{class:'num'+(iso===todayIso?' today':''),text:d}));
if(w){c.append(h('button',{class:'thumb pol',style:`--t:${tilt(w.id)}deg`,'aria-label':`Open journal page for ${fmt(iso,true)}`,onclick:()=>location.hash='#/journal/'+w.id},w.thumbnail_discovery_id&&h('img',{src:photo(w.thumbnail_discovery_id),alt:''})));
if(w.photo_count>1){const b=h('button',{class:'badge','aria-label':`Choose which of ${w.photo_count} photos shows on ${fmt(iso,true)}`,text:'+'+(w.photo_count-1)});
b.onclick=()=>pickPhoto(w.id,w.thumbnail_discovery_id,`Calendar thumbnail · ${fmt(iso,false,true)}${w.area?' · '+w.area:''}`,async id=>{await api(`/api/walks/${w.id}/thumbnail`,{method:'PUT',json:{discovery_id:id}});load()},b).catch(e=>say(e.message));c.append(b)}}
cells.push(c)}
const go=d=>()=>{m+=d;if(m<0){m=11;y--}if(m>11){m=0;y++}load()};
calEl.replaceChildren(h('div',{class:'top'},h('button',{class:'rnd','aria-label':'Previous month',text:'‹',onclick:go(-1)}),h('h2',{},MONTHS[m],' ',h('small',{class:'quiet',text:y})),h('button',{class:'rnd','aria-label':'Next month',text:'›',onclick:go(1)})),
h('div',{class:'days'},['SUN','MON','TUE','WED','THU','FRI','SAT'].map(x=>h('div',{class:'dow',text:x})),cells),
h('p',{class:'hint',text:'Click a day to open its journal page. Click the +N on a day to choose which photo shows.'}))}
el.replaceChildren(h('div',{class:'home'},
h('section',{class:'sheet'},h('p',{class:'quiet',text:`${greet}${nm?', '+nm:''}.`}),h('h1',{text:fmt(todayIso)}),h('p',{class:'quiet',text:LINES[(now.getDate()+m)%LINES.length]}),
h('p',{},h('button',{class:'btn pri',style:'width:100%',text:'Open today’s walk',onclick:()=>openToday('walk')})),
h('p',{},h('button',{class:'btn',style:'width:100%',text:'Open today’s journal page',onclick:()=>openToday('journal')})),
h('div',{class:'sticky',text:'When you tap Send on your phone, the photos land in the walk for the day they were taken.'}),collectionLine(col)),calEl));
await load()}