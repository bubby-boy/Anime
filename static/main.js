const display=document.getElementById('display');let expr='0';
document.querySelectorAll('.key').forEach(btn=>btn.addEventListener('click',()=>{
 const v=btn.dataset.value,action=btn.dataset.action;
 if(action==='clear'){expr='0';display.value=expr;return}
 if(action==='equals'){try{const safe=expr.replaceAll('×','*').replaceAll('÷','/').replaceAll('−','-');if(!/^[0-9+\-*/().\s]+$/.test(safe))throw Error();const result=Function('"use strict";return ('+safe+')')();if(!Number.isFinite(result))throw Error();expr=String(Number(result.toFixed(10)));display.value=expr}catch{display.value='Error';expr='0'}return}
 if(expr==='0'&&/[0-9.(]/.test(v))expr=v;else expr+=v;display.value=expr;
}));
document.getElementById('solve').addEventListener('click',()=>{
 const a=Number(document.getElementById('a').value),b=Number(document.getElementById('b').value),c=Number(document.getElementById('c').value),out=document.getElementById('solution');
 if(![a,b,c].every(Number.isFinite)){out.textContent='Enter valid numbers.';return}
 if(a===0){out.textContent=b===c?'Every value of x is a solution.':'No solution: the x coefficient is zero.';return}
 const x=(c-b)/a;out.textContent=`${a}x + ${b} = ${c}  →  x = ${Number(x.toFixed(10))}`;
});
async function tryUnlock(){const input=document.getElementById('about-entry'),msg=document.getElementById('about-message');msg.textContent='';try{const r=await fetch('/api/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:input.value})});if(!r.ok)throw Error();location.assign('/hidden-library')}catch{msg.textContent='';input.value='';input.placeholder='';input.setAttribute('aria-label','');}}
document.getElementById('about-submit').addEventListener('click',tryUnlock);
document.getElementById('about-entry').addEventListener('keydown',e=>{if(e.key==='Enter')tryUnlock()});
