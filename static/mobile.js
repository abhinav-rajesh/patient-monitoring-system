/* ─── MedWatch Mobile JS ─────────────────────────────────────────────────────
   Manages:
   1. Service Worker registration
   2. Web Push subscription with VAPID
   3. Socket.IO real-time alert feed
   4. SOS, sound toggle, clear alerts
──────────────────────────────────────────────────────────────────────────── */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let totalAlerts    = 0;
let criticalAlerts = 0;
let warningAlerts  = 0;
let soundEnabled   = true;
const MAX_ALERTS   = 50;
let alertCards     = [];
let selectedNurse  = 'nurse1';   // default
let pushSubscription = null;     // PushSubscription object

// ── Helper: convert VAPID base64 key to Uint8Array ───────────────────────────
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64  = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
}

// ═════════════════════════════════════════════════════════════════════════════
//  SERVICE WORKER + PUSH SETUP
// ═════════════════════════════════════════════════════════════════════════════

async function initServiceWorker() {
  if (!('serviceWorker' in navigator)) {
    showPushStatus('error', '❌ Service Workers not supported in this browser');
    return;
  }
  if (!('PushManager' in window)) {
    showPushStatus('error', '❌ Push API not supported in this browser');
    return;
  }

  try {
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    console.log('[SW] Registered, scope:', reg.scope);
    showPushStatus('idle', '🔔 Service Worker ready – tap Enable to subscribe');

    // Check if already subscribed
    await reg.update();
    const existingSub = await reg.pushManager.getSubscription();
    if (existingSub) {
      pushSubscription = existingSub;
      onSubscribed(existingSub);
    }
  } catch (err) {
    console.error('[SW] Registration failed:', err);
    showPushStatus('error', `❌ SW Error: ${err.message}`);
  }
}

// ── Enable Push Notifications ─────────────────────────────────────────────────
async function enablePushNotifications() {
  const btn = document.getElementById('btnEnablePush');

  // If already subscribed → toggle off (unsubscribe)
  if (pushSubscription) {
    await unsubscribePush();
    return;
  }

  setBtnLoading(true);

  try {
    // 1. Request notification permission
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      showPushStatus('error', '❌ Notification permission denied. Please allow in browser settings.');
      setBtnLoading(false);
      return;
    }

    // 2. Get SW registration
    const reg = await navigator.serviceWorker.ready;

    // 3. Subscribe to Push
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly:      true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
    });

    pushSubscription = sub;

    // 4. Send subscription to Flask server
    const resp = await fetch('/api/push/subscribe', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        subscription: sub.toJSON(),
        nurse_id:     selectedNurse,
      }),
    });

    if (!resp.ok) throw new Error(`Server error ${resp.status}`);

    onSubscribed(sub);

  } catch (err) {
    console.error('[Push] Subscribe error:', err);
    showPushStatus('error', `❌ Failed: ${err.message}`);
    setBtnLoading(false);
  }
}

function onSubscribed(sub) {
  setBtnLoading(false);

  // Update UI
  const btn = document.getElementById('btnEnablePush');
  if (btn) {
    btn.style.background = 'linear-gradient(135deg, #dc2626, #991b1c)';
    btn.innerHTML = '<span>🔕</span><span>Disable Push Notifications</span>';
  }

  document.getElementById('btnTestPush').style.display = 'flex';
  showPushStatus('active', `✅ Push active for ${selectedNurse} – you'll receive OS alerts!`);

  // Lock nurse selector
  document.getElementById('pushNurseRow').style.opacity = '0.5';
  document.getElementById('pushNurseRow').style.pointerEvents = 'none';

  console.log('[Push] Subscribed:', sub.endpoint.slice(0, 50) + '…');
}

async function unsubscribePush() {
  if (!pushSubscription) return;
  try {
    await fetch('/api/push/unsubscribe', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        nurse_id: selectedNurse,
        endpoint: pushSubscription.endpoint,
      }),
    });
    await pushSubscription.unsubscribe();
  } catch (err) { console.error('[Push] Unsubscribe error:', err); }

  pushSubscription = null;

  const btn = document.getElementById('btnEnablePush');
  if (btn) {
    btn.style.background = '';
    btn.innerHTML = '<span>🔔</span><span>Enable Push Notifications</span>';
  }
  document.getElementById('btnTestPush').style.display = 'none';
  showPushStatus('idle', '🔔 Unsubscribed from push notifications');

  document.getElementById('pushNurseRow').style.opacity    = '';
  document.getElementById('pushNurseRow').style.pointerEvents = '';
}

// ── Test Push ─────────────────────────────────────────────────────────────────
async function sendTestPush() {
  const btn = document.getElementById('btnTestPush');
  if (btn) { btn.textContent = 'Sending…'; btn.disabled = true; }

  try {
    const resp = await fetch('/api/push/send-test', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nurse_id: selectedNurse }),
    });
    const data = await resp.json();
    console.log('[Push] Test sent:', data);
    if (btn) { btn.textContent = '✅ Sent! Check your notifications'; }
  } catch (err) {
    console.error('[Push] Test error:', err);
    if (btn) { btn.textContent = '❌ Failed'; }
  }

  setTimeout(() => {
    if (btn) { btn.textContent = 'Send Test Notification'; btn.disabled = false; }
  }, 4000);
}

// ── Nurse selector ────────────────────────────────────────────────────────────
function selectNurse(nurseId) {
  selectedNurse = nurseId;
  document.querySelectorAll('.nurse-select-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(`nsBtnNurse${nurseId.replace('nurse','')}`).classList.add('active');
  console.log('[Push] Selected nurse:', nurseId);
}

// ── Push status UI ────────────────────────────────────────────────────────────
function showPushStatus(state, message) {
  const dot  = document.getElementById('pushStatusDot');
  const text = document.getElementById('pushStatusText');
  if (!dot || !text) return;
  text.textContent = message;
  dot.className = 'push-status-dot';
  if (state === 'active') dot.classList.add('active');
  if (state === 'error')  dot.classList.add('error');
}

function setBtnLoading(loading) {
  const btn = document.getElementById('btnEnablePush');
  if (!btn) return;
  if (loading) {
    btn.innerHTML = '<span class="spinner"></span><span>Subscribing…</span>';
    btn.disabled  = true;
  } else {
    btn.disabled  = false;
  }
}

// ═════════════════════════════════════════════════════════════════════════════
//  SOCKET.IO – Real-Time In-App Alerts
// ═════════════════════════════════════════════════════════════════════════════

const socket = io({ transports: ['websocket', 'polling'] });

socket.on('connect', () => {
  setConnStatus('connected', 'Live');
  updateBanner(false);
});

socket.on('disconnect', () => setConnStatus('disconnected', 'Offline'));

socket.on('alert_event', (alert) => {
  addAlert(alert);
});

socket.on('vital_update', (data) => {
  if (data.alerts && data.alerts.length > 0) {
    updateBanner(data.alerts.some(a => a.level === 'critical'));
  }
});

// ── Connection Status ─────────────────────────────────────────────────────────
function setConnStatus(state, text) {
  const dot = document.querySelector('#mobileConnStatus .dot');
  const txt = document.getElementById('mobileConnText');
  if (dot) dot.className = `dot ${state}`;
  if (txt) txt.textContent = text;
}

function updateBanner(hasCritical) {
  const banner = document.getElementById('mobileStatusBanner');
  if (!banner) return;
  const icon  = banner.querySelector('.banner-icon');
  const title = banner.querySelector('.banner-title');
  const sub   = banner.querySelector('.banner-sub');

  if (hasCritical) {
    banner.classList.add('has-critical');
    icon.textContent  = '🚨';
    title.textContent = 'Critical Alert Active';
    sub.textContent   = 'Immediate attention required!';
  } else {
    banner.classList.remove('has-critical');
    icon.textContent  = '🔔';
    title.textContent = 'Monitoring Active';
    sub.textContent   = 'Real-time patient alerts appear here';
  }
}

// ── Add Alert Card ────────────────────────────────────────────────────────────
function addAlert(alert) {
  const list    = document.getElementById('mobileAlertList');
  const emptyEl = document.getElementById('mobileEmptyState');
  if (!list) return;

  if (emptyEl) emptyEl.style.display = 'none';

  totalAlerts++;
  if (alert.level === 'critical') criticalAlerts++;
  else warningAlerts++;
  updateCounters();

  const isCrit = alert.level === 'critical';
  const icon   = isCrit ? '🚨' : '⚠️';
  const now    = new Date().toLocaleTimeString('en-GB');

  const card = document.createElement('div');
  card.className = `mobile-alert-card ${alert.level}`;
  card.innerHTML = `
    <span class="alert-card-icon">${icon}</span>
    <div class="alert-card-body">
      <div class="alert-card-title">${isCrit ? '🔴 Critical Alert' : '🟡 Warning Alert'}</div>
      <div class="alert-card-meta"><strong>${alert.patient_name}</strong> — ${alert.vital}</div>
      <span class="alert-card-value">${alert.value} · ${alert.direction}</span>
      <div class="alert-card-time">⏰ ${now}</div>
    </div>
  `;
  list.prepend(card);
  alertCards.push(card);
  if (alertCards.length > MAX_ALERTS) alertCards.shift()?.remove();

  if (soundEnabled) playSound(isCrit);
  if (navigator.vibrate) navigator.vibrate(isCrit ? [200, 100, 200] : [150]);
}

function updateCounters() {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('mTotalAlerts',    totalAlerts);
  set('mCriticalAlerts', criticalAlerts);
  set('mWarningAlerts',  warningAlerts);
}

function clearAlerts() {
  const list = document.getElementById('mobileAlertList');
  if (list) {
    list.innerHTML = `
      <div class="mobile-empty-state" id="mobileEmptyState">
        <div class="empty-icon">🩺</div>
        <p>All patients are stable</p>
        <p class="empty-sub">Alerts will appear here instantly</p>
      </div>`;
  }
  alertCards = [];
  totalAlerts = criticalAlerts = warningAlerts = 0;
  updateCounters();
  updateBanner(false);
}

function triggerSOS() {
  const btn = document.getElementById('sosBtn');
  if (btn) {
    btn.style.background = 'linear-gradient(135deg, #7f1d1d, #991b1b)';
    btn.innerHTML = `<span class="sos-icon">🆘</span><span>SOS Sent!</span>`;
  }
  const flash = document.createElement('div');
  flash.style.cssText = `position:fixed;inset:0;background:rgba(220,38,38,0.25);z-index:9999;pointer-events:none;animation:flashSOS 0.5s ease 3;`;
  document.body.appendChild(flash);
  setTimeout(() => flash.remove(), 1500);
  if (navigator.vibrate) navigator.vibrate([300, 100, 300, 100, 300]);
  setTimeout(() => {
    if (btn) {
      btn.style.background = '';
      btn.innerHTML = `<span class="sos-icon">🆘</span><span>Emergency SOS</span>`;
    }
  }, 3000);
  alert('🆘 Emergency SOS triggered! Medical staff notified.');
}

function toggleSound() {
  soundEnabled = !soundEnabled;
  const btn = document.getElementById('soundToggle');
  if (btn) {
    btn.textContent = soundEnabled ? '🔔' : '🔕';
    btn.classList.toggle('muted', !soundEnabled);
  }
}

function playSound(critical) {
  try {
    const ctx  = new (window.AudioContext || window.webkitAudioContext)();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.value = critical ? 880 : 660;
    gain.gain.setValueAtTime(0.25, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.8);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.8);
  } catch (_) {}
}

// Add CSS flash for SOS
const style = document.createElement('style');
style.textContent = `@keyframes flashSOS { 0%,100%{opacity:0} 50%{opacity:1} }`;
document.head.appendChild(style);

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initServiceWorker();
});
