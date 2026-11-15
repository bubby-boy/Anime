async function api(url,opts={}){const r=await fetch(url,{...opts,headers:{'Content-Type':'application/json',...(opts.headers||{})}});if(!r.ok)throw Error('Request failed ('+r.status+')');return r.json();}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function act(id,action,extra){if(!confirm('Confirm '+action.replaceAll('-',' ')+' for user #'+id+'?'))return;await api(`/api/admin/users/${id}/${action}`,{method:'POST',body:JSON.stringify(extra||{})});await refresh();}
async function refresh(){
 const [o,u,a,s]=await Promise.all([api('/api/admin/overview'),api('/api/admin/users'),api('/api/admin/activity?limit=100'),api('/api/admin/sessions')]);
 document.getElementById('s-users').textContent=o.users;document.getElementById('s-online').textContent=o.online;document.getElementById('s-events').textContent=o.events;document.getElementById('s-completed').textContent=o.completed;
 document.getElementById('users').innerHTML=u.map(x=>`<tr><td><strong>${esc(x.username)}</strong><small>#${x.id}</small></td><td>${esc(x.role)} · ${esc(x.status)}</td><td>${esc(x.last_seen||'Never')}</td><td>${x.favorites}</td><td>${x.watched}</td><td class="actions"><button onclick="act(${x.id},'kick')">Kick</button><button onclick="act(${x.id},'${x.status==='banned'?'unban':'ban'}')">${x.status==='banned'?'Unban':'Ban'}</button><button onclick="act(${x.id},'reset-progress')">Reset progress</button><button onclick="act(${x.id},'clear-favorites')">Clear favorites</button><button class="danger" onclick="act(${x.id},'delete')">Delete</button></td></tr>`).join('');
 document.getElementById('activity').innerHTML=a.map(x=>`<div class="activity-row"><div><strong>${esc(x.action)}</strong><span>${esc(x.username||'Visitor')} · ${esc(x.details)}</span></div><div class="activity-meta"><b>${esc(x.ip_address)}</b><time>${esc(x.created_at)}</time></div></div>`).join('')||'<p class="muted">No events yet.</p>';
 document.getElementById('sessions').innerHTML=s.slice(0,100).map(x=>`<tr><td>${esc(x.username)}</td><td>${esc(x.ip_address)}</td><td>${esc(x.user_agent)}</td><td>${esc(x.last_seen)}</td><td>${x.revoked?'Revoked':'Active'}</td></tr>`).join('');
 updatePopupUsers(u);
 await refreshPopups();
}
document.getElementById('refresh').onclick=()=>refresh().catch(e=>alert(e.message));
refresh().catch(e=>document.getElementById('activity').textContent=e.message);
setInterval(()=>refresh().catch(e=>document.getElementById('popup-admin-status').textContent=e.message),5000);

function updatePopupUsers(users) {
 const select=document.getElementById('popup-user'), selected=select.value;
 const options=users.filter(user=>user.status==='active').map(user=>`<option value="${user.id}">${esc(user.username)} (#${user.id})</option>`).join('');
 const html='<option value="">Select a user</option>'+options;
 if(select.innerHTML!==html){select.innerHTML=html;select.value=selected;}
}
async function popupRequest(url, data) {
 const response=await fetch(url, data===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const result=await response.json();
 if(!response.ok)throw Error(result.error||`Request failed (${response.status}).`);
 return result;
}
async function refreshPopups() {
 const user=document.getElementById('popup-user').value, history=document.getElementById('popup-history');
 if(!user){history.textContent='Select a user to view messages.';return;}
 const messages=await popupRequest(`/api/admin/users/${user}/popups`);
 if(document.getElementById('popup-user').value!==user)return;
 history.innerHTML=messages.map(p=>{
   const active=!p.dismissed_at&&!p.released_at;
   const status=p.released_at?'Released':p.dismissed_at?'Dismissed':p.received_at?'Received by user page':'Pending delivery';
   const modes={once:'Pause once',dismiss:'Until user dismisses',admin:'Until administrator releases'};
   return `<article class="popup-history-item"><h4>${esc(p.title)}</h4><p class="screen-text">${esc(p.message)}</p><p class="muted">#${p.id} | ${esc(status)} | ${esc(modes[p.pause_mode])} | ${esc(p.created_at)}</p><p class="screen-text"><strong>User response:</strong> ${p.reply===null?'No response yet.':esc(p.reply)}</p>${active?`<button type="button" data-release-popup="${p.id}">Release / close popup</button>`:''}</article>`;
 }).join('')||'No popups sent to this user.';
}
document.getElementById('popup-user').addEventListener('change',()=>{
 document.getElementById('popup-admin-status').textContent='';
 refreshPopups().catch(e=>document.getElementById('popup-admin-status').textContent=e.message);
});
document.getElementById('admin-popup-form').addEventListener('submit',async event=>{
 event.preventDefault();
 const status=document.getElementById('popup-admin-status'), button=document.getElementById('popup-send');
 button.disabled=true;status.textContent='';
 try{
   const user=document.getElementById('popup-user').value;
   await popupRequest(`/api/admin/users/${user}/popups`,{title:document.getElementById('popup-title').value,message:document.getElementById('popup-message').value,pause_mode:document.getElementById('popup-mode').value});
   status.textContent='Popup sent. Delivery status and responses update below.';
   await refreshPopups();
 }catch(error){status.textContent=error.message;}
 finally{button.disabled=false;}
});
document.getElementById('popup-history').addEventListener('click',async event=>{
 const button=event.target.closest('[data-release-popup]');
 if(!button)return;
 button.disabled=true;
 try{
   await popupRequest(`/api/admin/popups/${button.dataset.releasePopup}/release`,{});
   document.getElementById('popup-admin-status').textContent='Released. The user can resume playback manually.';
   await refreshPopups();
 }catch(error){document.getElementById('popup-admin-status').textContent=error.message;button.disabled=false;}
});
