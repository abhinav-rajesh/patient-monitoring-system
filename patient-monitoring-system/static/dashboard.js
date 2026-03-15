/* ─── Dashboard JavaScript ───────────────────────────────────────────────────
   Connects to Socket.IO, updates vitals, renders Chart.js graphs, triggers alerts
──────────────────────────────────────────────────────────────────────────── */

'use strict';

// ── Config ────────────────────────────────────────────────────────────────────
const THRESHOLDS   = JSON.parse(document.getElementById('thresholdData').textContent);
const NORMAL_RANGE = JSON.parse(document.getElementById('normalRangeData').textContent);
const PATIENTS     = JSON.parse(document.getElementById('patientData').textContent);

const MAX_POINTS   = 30;  // data points shown on chart
const TOAST_LIMIT  = 5;   // max simultaneous toasts

// ── State ─────────────────────────────────────────────────────────────────────
const charts       = {};   // Chart.js instances keyed by patient id
const chartData    = {};   // rolling data arrays keyed by patient id
const alertCount   = { total: 0, critical: 0, warning: 0 };
let   toastQueue   = [];
let   alertFeedItems = [];
const SOUND_ENABLED = { v: true };

// ── Socket.IO ─────────────────────────────────────────────────────────────────
const socket = io({ transports: ['websocket', 'polling'] });

socket.on('connect', () => {
  setConnStatus('connected', 'Live');
  socket.emit('subscribe_nurse', { nurse_id: NURSE_ID });
});

socket.on('disconnect', () => {
  setConnStatus('disconnected', 'Offline');
});

socket.on('connect_error', () => {
  setConnStatus('disconnected', 'Error');
});

socket.on('vital_update', (data) => {
  const pid = data.patient_id;
  if (!isMyPatient(pid)) return;
  updateVitals(pid, data.vitals);
  updateChart(pid, data.vitals.heart_rate);
  updateCardStatus(pid, data.alerts);
});

socket.on('alert_event', (alert) => {
  if (!isMyPatient(alert.patient_id)) return;
  showToast(alert);
  addFeedItem(alert);
  updateAlertCounts(alert);
  updateNavDot(alert.patient_id, alert.level);
  playAlertSound(alert.level);
});

// ── Patient Check ─────────────────────────────────────────────────────────────
const MY_IDS = PATIENTS.map(p => p.id);
function isMyPatient(pid) { return MY_IDS.includes(pid); }

// ── Clock ─────────────────────────────────────────────────────────────────────
function updateClock() {
  const el = document.getElementById('headerTime');
  if (!el) return;
  el.textContent = new Date().toLocaleTimeString('en-GB');
}
setInterval(updateClock, 1000);
updateClock();

// ── Connection Status UI ──────────────────────────────────────────────────────
function setConnStatus(state, text) {
  const dot = document.querySelector('#connStatus .dot');
  const txt = document.getElementById('connText');
  if (!dot || !txt) return;
  dot.className = `dot ${state}`;
  txt.textContent = text;
}

// ── Vital Update ──────────────────────────────────────────────────────────────
function updateVitals(pid, v) {
  safeSet(`hr-${pid}`,   formatVal(v.heart_rate,     0));
  safeSet(`spo2-${pid}`, formatVal(v.spo2,            1));
  safeSet(`temp-${pid}`, formatVal(v.temperature,     1));
  safeSet(`bp-${pid}`,   formatVal(v.blood_pressure,  0));

  // Progress bars (mapped to 0-100%)
  setBar(`hr-bar-${pid}`,   v.heart_rate,     0,   180, 'heart_rate');
  setBar(`spo2-bar-${pid}`, v.spo2,           80,  100, 'spo2');
  setBar(`temp-bar-${pid}`, v.temperature,    34,  42,  'temperature');
  setBar(`bp-bar-${pid}`,   v.blood_pressure, 50,  220, 'blood_pressure');

  // Coloring the values
  colorValue(`hr-${pid}`,   v.heart_rate,     THRESHOLDS.heart_rate);
  colorValue(`spo2-${pid}`, v.spo2,           THRESHOLDS.spo2);
  colorValue(`temp-${pid}`, v.temperature,    THRESHOLDS.temperature);
  colorValue(`bp-${pid}`,   v.blood_pressure, THRESHOLDS.blood_pressure);
}

function formatVal(n, decimals) {
  return parseFloat(n).toFixed(decimals);
}

function safeSet(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function setBar(id, value, min, max, vitalKey) {
  const el = document.getElementById(id);
  if (!el) return;
  const pct = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  el.style.width = pct + '%';

  const t = THRESHOLDS[vitalKey];
  if (value < t.low || value > t.high) {
    el.style.background = 'var(--red)';
  } else {
    const n = NORMAL_RANGE[vitalKey];
    if (value < n.low || value > n.high) {
      el.style.background = 'var(--yellow)';
    } else {
      el.style.background = 'var(--cyan)';
    }
  }
}

function colorValue(id, value, thresh) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('alerting', 'warning');
  if (value < thresh.low || value > thresh.high) {
    el.classList.add('alerting');
  }
}

// ── Tile alerting state ───────────────────────────────────────────────────────
function updateCardStatus(pid, alerts) {
  const card  = document.getElementById(`card-${pid}`);
  const badge = document.getElementById(`badge-${pid}`);
  const banner = document.getElementById(`alert-banner-${pid}`);
  const bannerText = document.getElementById(`alert-banner-text-${pid}`);

  if (!card || !badge) return;

  // Clear all vital tiles first
  ['heart_rate', 'spo2', 'temperature', 'blood_pressure'].forEach(vk => {
    const tile = document.getElementById(`tile-${vk}-${pid}`);
    if (tile) tile.classList.remove('alerting', 'warning');
  });

  card.classList.remove('card-alert', 'card-warning');
  badge.className = 'card-status-badge';
  badge.querySelector('.badge-text').textContent = 'STABLE';
  if (banner) banner.style.display = 'none';

  if (!alerts || alerts.length === 0) return;

  const hasCritical = alerts.some(a => a.level === 'critical');
  const level = hasCritical ? 'critical' : 'warning';

  // Mark card
  card.classList.add(hasCritical ? 'card-alert' : 'card-warning');
  badge.classList.add(level);
  badge.querySelector('.badge-text').textContent = hasCritical ? 'CRITICAL' : 'WARNING';

  // Mark tiles
  const vitalKeyMap = {
    'Heart Rate': 'heart_rate', 'SpO₂': 'spo2',
    'Temperature': 'temperature', 'Blood Pressure': 'blood_pressure'
  };
  alerts.forEach(a => {
    const vk = Object.entries(vitalKeyMap).find(([lbl]) => a.vital.startsWith(lbl.split('₂')[0]))?.[1];
    if (vk) {
      const tile = document.getElementById(`tile-${vk}-${pid}`);
      if (tile) tile.classList.add(a.level === 'critical' ? 'alerting' : 'warning');
    }
  });

  // Alert banner
  if (banner && bannerText) {
    const msgs = alerts.map(a => `${a.vital}: ${a.value} (${a.direction})`);
    bannerText.textContent = '⚠ ' + msgs.join(' · ');
    banner.style.display = 'flex';
  }
}

// ── Chart.js Setup ────────────────────────────────────────────────────────────
function initCharts() {
  PATIENTS.forEach(p => {
    const pid = p.id;
    const canvas = document.getElementById(`chart-${pid}`);
    if (!canvas) return;

    chartData[pid] = {
      labels: Array(MAX_POINTS).fill(''),
      data:   Array(MAX_POINTS).fill(null)
    };

    const ctx = canvas.getContext('2d');

    // Create gradient fill
    const grad = ctx.createLinearGradient(0, 0, 0, 100);
    grad.addColorStop(0,   'rgba(6,182,212,0.35)');
    grad.addColorStop(1,   'rgba(6,182,212,0.00)');

    charts[pid] = new Chart(ctx, {
      type: 'line',
      data: {
        labels:   chartData[pid].labels,
        datasets: [{
          label:                'Heart Rate',
          data:                 chartData[pid].data,
          borderColor:          '#06b6d4',
          borderWidth:          2,
          pointRadius:          0,
          pointHoverRadius:     4,
          tension:              0.45,
          fill:                 true,
          backgroundColor:      grad,
        }]
      },
      options: {
        responsive:          true,
        maintainAspectRatio: false,
        animation:           { duration: 400, easing: 'easeInOutQuart' },
        interaction:         { mode: 'nearest', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(15,23,42,0.95)',
            borderColor:     'rgba(6,182,212,0.3)',
            borderWidth:     1,
            titleColor:      '#94a3b8',
            bodyColor:       '#f1f5f9',
            callbacks: {
              label: ctx => `${ctx.parsed.y?.toFixed(0)} bpm`
            }
          }
        },
        scales: {
          x: {
            display: false,
            grid:    { display: false }
          },
          y: {
            min:    40,
            max:    160,
            border: { display: false },
            grid: {
              color:     'rgba(255,255,255,0.04)',
              drawTicks: false
            },
            ticks: {
              color:     'rgba(255,255,255,0.25)',
              font:      { size: 9 },
              maxTicksLimit: 4,
              padding:   6
            }
          }
        }
      }
    });
  });
}

function updateChart(pid, heartRate) {
  const chart = charts[pid];
  const store = chartData[pid];
  if (!chart || !store) return;

  store.data.push(heartRate);
  store.labels.push('');

  if (store.data.length > MAX_POINTS) {
    store.data.shift();
    store.labels.shift();
  }

  chart.data.datasets[0].data = [...store.data];
  chart.data.labels           = [...store.labels];

  // Change line colour when alerting
  const t = THRESHOLDS.heart_rate;
  if (heartRate < t.low || heartRate > t.high) {
    chart.data.datasets[0].borderColor = '#ef4444';
  } else {
    chart.data.datasets[0].borderColor = '#06b6d4';
  }

  chart.update('none');
}

// ── Toast Notifications ───────────────────────────────────────────────────────
function showToast(alert) {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  // Trim old toasts
  if (toastQueue.length >= TOAST_LIMIT) {
    const removed = toastQueue.shift();
    removed?.remove();
  }

  const isCrit = alert.level === 'critical';
  const icon   = isCrit ? '🚨' : '⚠️';
  const now = new Date().toLocaleTimeString('en-GB');

  const toast = document.createElement('div');
  toast.className = `toast ${alert.level}`;
  toast.innerHTML = `
    <span class="toast-icon">${icon}</span>
    <div style="flex:1">
      <div class="toast-title">${isCrit ? 'Critical' : 'Warning'} — ${alert.patient_name}</div>
      <div class="toast-body">${alert.vital}: ${alert.value} (${alert.direction})</div>
      <div class="toast-time">${now}</div>
    </div>
    <button class="toast-close" onclick="this.closest('.toast').remove()">×</button>
  `;
  container.prepend(toast);
  toastQueue.push(toast);

  // Auto-dismiss
  setTimeout(() => {
    toast.style.opacity   = '0';
    toast.style.transform = 'translateX(60px)';
    toast.style.transition = 'all 0.4s ease';
    setTimeout(() => {
      toast.remove();
      toastQueue = toastQueue.filter(t => t !== toast);
    }, 400);
  }, isCrit ? 8000 : 5000);
}

// ── Alert Feed (Sidebar) ──────────────────────────────────────────────────────
function addFeedItem(alert) {
  const feed = document.getElementById('alertFeed');
  if (!feed) return;

  const emptyMsg = feed.querySelector('.feed-empty');
  if (emptyMsg) emptyMsg.remove();

  const now = new Date().toLocaleTimeString('en-GB');
  const item = document.createElement('div');
  item.className = `feed-item ${alert.level}`;
  item.innerHTML = `
    <div class="feed-name">${alert.patient_name}</div>
    <div class="feed-detail">${alert.vital}: ${alert.value} (${alert.direction})</div>
    <div class="feed-time">${now}</div>
  `;
  feed.prepend(item);

  alertFeedItems.push(item);
  // Keep max 15 items
  if (alertFeedItems.length > 15) {
    const old = alertFeedItems.shift();
    old?.remove();
  }
}

// ── Alert Counts ──────────────────────────────────────────────────────────────
function updateAlertCounts(alert) {
  alertCount.total++;
  if (alert.level === 'critical') alertCount.critical++;
  else alertCount.warning++;

  const el = document.getElementById('statAlerts');
  if (el) el.textContent = alertCount.total;
}

// ── Sidebar Nav Dot ───────────────────────────────────────────────────────────
function updateNavDot(pid, level) {
  const dot = document.getElementById(`nav-dot-${pid}`);
  if (!dot) return;
  if (level === 'critical' || level === 'warning') {
    dot.classList.add('alerting');
  }
}

// ── Scroll to Patient ─────────────────────────────────────────────────────────
function scrollToPatient(pid) {
  const card = document.getElementById(`card-${pid}`);
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ── Alert Sound ───────────────────────────────────────────────────────────────
function playAlertSound(level) {
  if (!SOUND_ENABLED.v) return;
  try {
    const ctx  = new (window.AudioContext || window.webkitAudioContext)();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type      = 'sine';
    osc.frequency.value = level === 'critical' ? 880 : 660;
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.6);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.6);
  } catch (_) { /* Audio API not available */ }
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initCharts();
});
