async function api(url, method='GET', body=null) {
    const opts = {method, headers:{'Content-Type':'application/json'}};
    if (body) opts.body = JSON.stringify(body);
    try {
        const res = await fetch(url, opts);
        if (res.status === 401) { window.location.href='/login'; return; }
        if (!res.ok) {
            const err = await res.json().catch(()=>({}));
            showToast(err.detail || 'Something went wrong', 'error');
            throw new Error(err.detail || res.statusText);
        }
        return await res.json();
    } catch(e) { console.error('API Error:', e); throw e; }
}
function gv(id) { return document.getElementById(id).value; }
function openModal(html) {
    const o = document.getElementById('modal-overlay'), c = document.getElementById('modal-content');
    c.innerHTML = html; o.classList.remove('hidden'); o.classList.add('flex'); document.body.style.overflow='hidden';
    setTimeout(() => { const i = c.querySelector('input:not([type="hidden"]):not([type="checkbox"]):not([type="date"]),textarea'); if(i) i.focus(); }, 100);
}
function closeModal(e) {
    if (e && e.target !== document.getElementById('modal-overlay')) return;
    document.getElementById('modal-overlay').classList.add('hidden');
    document.getElementById('modal-overlay').classList.remove('flex');
    document.body.style.overflow = '';
}
document.addEventListener('keydown', e => { if(e.key==='Escape') closeModal({target:document.getElementById('modal-overlay')}); });
function showToast(msg, type='success') {
    const t = document.getElementById('toast'); t.textContent = msg;
    t.className = `fixed top-4 right-4 px-4 py-2 rounded-xl shadow-lg transform transition-transform z-50 text-sm ${type==='error'?'bg-red-900 text-red-200':'bg-gray-800 text-white'}`;
    t.style.transform = 'translateX(0)';
    setTimeout(() => { t.style.transform = 'translateX(calc(100% + 1rem))'; }, 2500);
}
function escapeHtml(text) {
    if (!text) return '';
    const d = document.createElement('div'); d.textContent = text;
    return d.innerHTML.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}
let touchStartY = 0;
document.addEventListener('touchstart', e => { touchStartY = e.touches[0].clientY; }, {passive:true});
document.addEventListener('touchend', e => { if(e.changedTouches[0].clientY - touchStartY > 150 && window.scrollY === 0) location.reload(); }, {passive:true});
