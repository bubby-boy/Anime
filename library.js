const root=document.getElementById('series-groups'),search=document.getElementById('search'),seriesSelect=document.getElementById('series'),player=document.getElementById('player');
let items=[];
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function render(){
 const q=search.value.trim().toLowerCase(),chosen=seriesSelect.value;
 const filtered=items.filter(x=>(!chosen||x.series===chosen)&&(!q||(x.series+' '+x.title+' '+x.filename).toLowerCase().includes(q)));
 const groups={};filtered.forEach(x=>(groups[x.series]??=[]).push(x));
 const names=Object.keys(groups).sort((a,b)=>a.localeCompare(b));
 if(!names.length){root.innerHTML='<div class="empty">No matching episodes found.</div>';return}
 root.innerHTML=names.map(name=>`<section class="series-card"><div class="series-title"><h3>${esc(name)}</h3><span>${groups[name].length} LINKS</span></div><div class="episode-list">${groups[name].map(x=>`<div class="episode"><div class="episode-name" title="${esc(x.title)}">${esc(x.title)}</div><div class="episode-actions"><button data-play="${x.id}">Play</button><a href="/api/download/${x.id}">Download</a></div></div>`).join('')}</div></section>`).join('');
 root.querySelectorAll('[data-play]').forEach(b=>b.addEventListener('click',()=>play(b.dataset.play)));
}
function play(id){const x=items.find(i=>i.id===id);if(!x)return;player.src=x.url;player.load();player.play().catch(()=>{});document.getElementById('now-title').textContent=x.title;document.getElementById('now-series').textContent=x.series;player.scrollIntoView({behavior:'smooth',block:'center'});}
search.addEventListener('input',render);seriesSelect.addEventListener('change',render);
fetch('/api/catalog').then(r=>{if(!r.ok)throw Error();return r.json()}).then(data=>{items=data;document.getElementById('total').textContent=items.length;render()}).catch(()=>root.innerHTML='<div class="empty">The library could not be loaded. Return to the main page and unlock it again.</div>');
