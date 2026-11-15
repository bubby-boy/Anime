const root=document.getElementById('series-groups'),search=document.getElementById('search'),seriesSelect=document.getElementById('series'),player=document.getElementById('player');
let items=[],favorites=new Set(),progress={},current=null,saveTimer=null;
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function render(){
 const q=search.value.trim().toLowerCase(),chosen=seriesSelect.value;
 const filtered=items.filter(x=>(!chosen||x.series===chosen)&&(!q||(x.series+' '+x.title+' '+x.filename).toLowerCase().includes(q)));
 const groups={};filtered.forEach(x=>(groups[x.series]??=[]).push(x));
 const names=Object.keys(groups).sort((a,b)=>a.localeCompare(b));
 if(!names.length){root.innerHTML='<div class="empty">No matching episodes found.</div>';return}
 root.innerHTML=names.map(name=>`<section class="series-card"><div class="series-title"><h3>${esc(name)}</h3><span>${groups[name].length} LINKS</span></div><div class="episode-list">${groups[name].map(x=>`<div class="episode"><div class="episode-name" title="${esc(x.title)}">${favorites.has(x.id)?'★ ':''}${esc(x.title)}${progress[x.id]?.completed?' ✓':''}</div><div class="episode-actions"><button data-play="${x.id}">Play</button><button data-fav="${x.id}">${favorites.has(x.id)?'★':'☆'}</button><a href="/api/download/${x.id}">Download</a></div></div>`).join('')}</div></section>`).join('');
 root.querySelectorAll('[data-play]').forEach(b=>b.addEventListener('click',()=>play(b.dataset.play)));
 root.querySelectorAll('[data-fav]').forEach(b=>b.addEventListener('click',()=>toggleFavorite(b.dataset.fav)));
}
async function toggleFavorite(id){const d=await fetch('/api/favorites/'+id,{method:'POST'}).then(r=>r.json());if(d.favorite)favorites.add(id);else favorites.delete(id);render();if(current?.id===id)updateFavoriteButton();}
function updateFavoriteButton(){const b=document.getElementById('favorite-current');b.disabled=!current;b.textContent=current?(favorites.has(current.id)?'★ Favorited':'☆ Favorite'):'☆ Favorite';}
async function play(id){const x=items.find(i=>i.id===id);if(!x)return;current=x;player.src=x.url;player.load();const p=progress[id];if(p?.position>5&&!p.completed){player.addEventListener('loadedmetadata',()=>{player.currentTime=Math.min(p.position,Math.max(0,player.duration-2));},{once:true});document.getElementById('resume-note').textContent='Saved progress found — resuming near '+Math.floor(p.position/60)+'m '+Math.floor(p.position%60)+'s.';}else document.getElementById('resume-note').textContent='';player.play().catch(()=>{});document.getElementById('now-title').textContent=x.title;document.getElementById('now-series').textContent=x.series;updateFavoriteButton();fetch('/api/track/play/'+id,{method:'POST'});player.scrollIntoView({behavior:'smooth',block:'center'});}
document.getElementById('favorite-current').addEventListener('click',()=>current&&toggleFavorite(current.id));
async function saveProgress(){if(!current||!Number.isFinite(player.duration))return;const body={position:player.currentTime,duration:player.duration,completed:player.ended};try{await fetch('/api/progress/'+current.id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});progress[current.id]={...body,completed:body.completed||(body.duration>0&&body.position/body.duration>=.95)};}catch{}}
player.addEventListener('timeupdate',()=>{if(!current)return;clearTimeout(saveTimer);saveTimer=setTimeout(saveProgress,3500);});
player.addEventListener('pause',saveProgress);player.addEventListener('ended',saveProgress);
search.addEventListener('input',render);seriesSelect.addEventListener('change',render);
Promise.all([fetch('/api/catalog').then(r=>{if(!r.ok)throw Error();return r.json()}),fetch('/api/favorites').then(r=>r.json()),fetch('/api/progress').then(r=>r.json())]).then(([data,f,p])=>{items=data;favorites=new Set(f);progress=Object.fromEntries(p.map(x=>[x.item_id,x]));document.getElementById('total').textContent=items.length;render();}).catch(()=>root.innerHTML='<div class="empty">Sign in and unlock the library to load your saved collection.</div>');
