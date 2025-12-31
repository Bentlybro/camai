// CAMAI Mobile App
// Simple vanilla JS app that works in both browser and Capacitor WebView

class CamaiApp {
  constructor() {
    this.serverUrl = null;
    this.isConnected = false;
    this.ws = null;
    this.statsInterval = null;
    this.pingInterval = null;
    this.systemStatsTimeout = null;
    this.currentTab = 'live';
    this.showOverlays = true;

    this.init();
  }

  init() {
    // Load saved settings from localStorage
    this.loadSettings();

    // Setup event listeners
    this.setupEventListeners();

    // Auto-connect if we have saved server
    if (this.serverUrl) {
      const urlParts = this.serverUrl.replace(/^https?:\/\//, '').split(':');
      document.getElementById('server-ip').value = urlParts[0];
      document.getElementById('server-port').value = urlParts[1] || '8080';
      this.connect();
    }
  }

  loadSettings() {
    try {
      const savedUrl = localStorage.getItem('camai_serverUrl');
      if (savedUrl) this.serverUrl = savedUrl;

      const savedOverlays = localStorage.getItem('camai_showOverlays');
      if (savedOverlays !== null) this.showOverlays = savedOverlays === 'true';

      document.getElementById('setting-overlays').checked = this.showOverlays;
    } catch (e) {
      console.log('Could not load settings:', e);
    }
  }

  saveSettings() {
    try {
      if (this.serverUrl) {
        localStorage.setItem('camai_serverUrl', this.serverUrl);
      }
      localStorage.setItem('camai_showOverlays', String(this.showOverlays));
    } catch (e) {
      console.log('Could not save settings:', e);
    }
  }

  setupEventListeners() {
    // Connect button
    document.getElementById('connect-btn').addEventListener('click', () => this.connect());

    // Enter key on inputs
    document.getElementById('server-ip').addEventListener('keypress', (e) => {
      if (e.key === 'Enter') this.connect();
    });
    document.getElementById('server-port').addEventListener('keypress', (e) => {
      if (e.key === 'Enter') this.connect();
    });

    // Tab navigation
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', (e) => this.switchTab(e.target.dataset.tab));
    });

    // Settings modal
    document.getElementById('settings-btn').addEventListener('click', () => this.openSettings());
    document.getElementById('close-settings').addEventListener('click', () => this.closeSettings());
    document.getElementById('disconnect-btn').addEventListener('click', () => this.disconnect());

    // Settings changes
    document.getElementById('setting-confidence').addEventListener('input', (e) => {
      document.getElementById('confidence-value').textContent = e.target.value;
    });
    document.getElementById('setting-confidence').addEventListener('change', (e) => {
      this.updateDetectionSettings({ confidence: parseFloat(e.target.value) });
    });
    document.getElementById('setting-overlays').addEventListener('change', (e) => {
      this.showOverlays = e.target.checked;
      this.saveSettings();
      this.updateStream();
    });

    // Event filter
    document.getElementById('event-filter').addEventListener('change', () => this.loadEvents());
    document.getElementById('refresh-events').addEventListener('click', () => this.loadEvents());

    // PTZ controls
    document.querySelectorAll('.ptz-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const direction = e.currentTarget.dataset.direction;
        this.sendPTZCommand(direction);
      });
    });

    // Fullscreen
    document.getElementById('fullscreen-btn').addEventListener('click', () => this.toggleFullscreen());

    // Stream error handling
    document.getElementById('live-stream').addEventListener('error', () => {
      document.getElementById('stream-error').classList.remove('hidden');
    });
    document.getElementById('live-stream').addEventListener('load', () => {
      document.getElementById('stream-error').classList.add('hidden');
    });

    // Click outside modal to close
    document.getElementById('settings-modal').addEventListener('click', (e) => {
      if (e.target.id === 'settings-modal') {
        this.closeSettings();
      }
    });
  }

  async connect() {
    const ip = document.getElementById('server-ip').value.trim();
    const port = document.getElementById('server-port').value.trim() || '8080';

    if (!ip) {
      this.showStatus('Please enter server address', 'error');
      return;
    }

    this.showStatus('Connecting...', '');

    const url = `http://${ip}:${port}`;

    try {
      // Test connection with timeout
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000);

      const response = await fetch(`${url}/api/stats/summary`, {
        method: 'GET',
        signal: controller.signal
      });

      clearTimeout(timeoutId);

      if (!response.ok) throw new Error('Server not responding');

      this.serverUrl = url;
      this.isConnected = true;
      this.saveSettings();

      this.showStatus('Connected!', 'success');

      // Switch to main screen
      setTimeout(() => {
        document.getElementById('setup-screen').classList.remove('active');
        document.getElementById('main-screen').classList.add('active');
        this.startApp();
      }, 500);

    } catch (error) {
      console.error('Connection error:', error);
      if (error.name === 'AbortError') {
        this.showStatus('Connection timed out', 'error');
      } else {
        this.showStatus('Connection failed. Check address and try again.', 'error');
      }
    }
  }

  startApp() {
    // Start stream
    this.updateStream();

    // Setup WebSocket
    this.connectWebSocket();

    // Start polling
    this.statsInterval = setInterval(() => this.loadStats(), 2000);
    this.loadStats();
    this.loadEvents();
    this.loadSystemStats();
    this.checkPTZ();

    // Update server display
    document.getElementById('setting-server').textContent = this.serverUrl;
  }

  updateStream() {
    const stream = document.getElementById('live-stream');
    const endpoint = this.showOverlays ? '/stream' : '/clean-stream';
    // Add timestamp to force refresh
    stream.src = `${this.serverUrl}${endpoint}?t=${Date.now()}`;
  }

  connectWebSocket() {
    if (this.ws) {
      this.ws.close();
    }

    const wsUrl = this.serverUrl.replace('http', 'ws') + '/ws';

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        console.log('WebSocket connected');
        this.updateConnectionStatus(true);
      };

      this.ws.onclose = () => {
        console.log('WebSocket disconnected');
        this.updateConnectionStatus(false);
        // Reconnect after 3 seconds
        setTimeout(() => {
          if (this.isConnected) this.connectWebSocket();
        }, 3000);
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'stats') {
            this.updateLiveStats(data);
          }
        } catch (e) {
          // Ignore parse errors
        }
      };

      // Clear any existing ping interval
      if (this.pingInterval) clearInterval(this.pingInterval);

      // Send ping every 30 seconds
      this.pingInterval = setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30000);

    } catch (e) {
      console.error('WebSocket setup error:', e);
    }
  }

  updateConnectionStatus(connected) {
    const dot = document.querySelector('.connection-dot');
    const status = document.getElementById('header-status');

    if (connected) {
      dot.classList.remove('disconnected');
      status.textContent = 'Connected';
    } else {
      dot.classList.add('disconnected');
      status.textContent = 'Reconnecting...';
    }
  }

  async loadStats() {
    if (!this.isConnected) return;

    try {
      const response = await fetch(`${this.serverUrl}/api/stats/summary`);
      const data = await response.json();

      // Update stats tab
      document.getElementById('stat-persons').textContent = data.person_events_today || 0;
      document.getElementById('stat-vehicles').textContent = data.vehicle_events_today || 0;
      document.getElementById('stat-packages').textContent = data.package_events_today || 0;
      document.getElementById('stat-total').textContent = data.events_today || 0;

      // Update uptime
      if (data.uptime) {
        document.getElementById('stat-uptime').textContent = this.formatUptime(data.uptime);
      }

      // Update performance
      document.getElementById('perf-fps').textContent = (data.fps || 0).toFixed(1);
      document.getElementById('perf-inference').textContent = `${(data.inference_time || 0).toFixed(1)} ms`;
      document.getElementById('perf-tracked').textContent = data.tracked_objects || 0;

      // Update stream overlay
      document.getElementById('stream-fps').textContent = `${(data.fps || 0).toFixed(1)} FPS`;
      document.getElementById('stream-latency').textContent = `${(data.inference_time || 0).toFixed(1)} ms`;

      // Load current detections
      await this.loadDetections();

    } catch (error) {
      console.error('Failed to load stats:', error);
    }
  }

  updateLiveStats(data) {
    if (data.fps) {
      document.getElementById('stream-fps').textContent = `${data.fps.toFixed(1)} FPS`;
      document.getElementById('perf-fps').textContent = data.fps.toFixed(1);
    }
    if (data.inference_time) {
      document.getElementById('stream-latency').textContent = `${data.inference_time.toFixed(1)} ms`;
      document.getElementById('perf-inference').textContent = `${data.inference_time.toFixed(1)} ms`;
    }
  }

  async loadDetections() {
    if (!this.isConnected) return;

    try {
      const response = await fetch(`${this.serverUrl}/api/stats/detections`);
      const data = await response.json();

      const container = document.getElementById('detections-list');

      if (!data.detections || data.detections.length === 0) {
        container.innerHTML = '<p class="empty-state">No active detections</p>';
        return;
      }

      container.innerHTML = data.detections.map(det => `
        <div class="detection-item ${det.class_name}">
          <div class="detection-icon ${det.class_name}">
            ${this.getClassIcon(det.class_name)}
          </div>
          <div class="detection-info">
            <div class="detection-type">${det.class_name}</div>
            <div class="detection-conf">${(det.confidence * 100).toFixed(0)}% confidence</div>
          </div>
        </div>
      `).join('');

    } catch (error) {
      console.error('Failed to load detections:', error);
    }
  }

  async loadEvents() {
    if (!this.isConnected) return;

    try {
      const filter = document.getElementById('event-filter').value;
      let url = `${this.serverUrl}/api/events?limit=50`;
      if (filter) url += `&type=${filter}`;

      const response = await fetch(url);
      const data = await response.json();

      const container = document.getElementById('events-list');

      if (!data.events || data.events.length === 0) {
        container.innerHTML = '<p class="empty-state">No events found</p>';
        return;
      }

      container.innerHTML = data.events.map(event => `
        <div class="event-item" data-id="${event.id}">
          <span class="event-type-badge ${event.class_name}">${event.class_name}</span>
          <div class="event-details">
            <div class="event-title">${this.formatEventType(event.type)}</div>
            <div class="event-time">${this.formatTime(event.timestamp)}</div>
          </div>
          <span class="event-confidence">${(event.confidence * 100).toFixed(0)}%</span>
        </div>
      `).join('');

    } catch (error) {
      console.error('Failed to load events:', error);
      document.getElementById('events-list').innerHTML = '<p class="empty-state">Failed to load events</p>';
    }
  }

  async loadSystemStats() {
    if (!this.isConnected) return;

    try {
      const endpoints = ['cpu', 'memory', 'gpu', 'disk', 'temperature'];
      const results = await Promise.all(
        endpoints.map(ep =>
          fetch(`${this.serverUrl}/api/system/${ep}`)
            .then(r => r.json())
            .catch(() => ({}))
        )
      );

      const [cpu, memory, gpu, disk, temp] = results;

      // CPU
      if (cpu.percent !== undefined) {
        document.getElementById('cpu-bar').style.width = `${cpu.percent}%`;
        document.getElementById('cpu-label').textContent = `${cpu.percent.toFixed(1)}%`;
      }

      // Memory
      if (memory.percent !== undefined) {
        document.getElementById('memory-bar').style.width = `${memory.percent}%`;
        const used = (memory.used / 1024 / 1024 / 1024).toFixed(1);
        const total = (memory.total / 1024 / 1024 / 1024).toFixed(1);
        document.getElementById('memory-label').textContent = `${used} / ${total} GB`;
      }

      // GPU
      if (gpu.utilization !== undefined) {
        document.getElementById('gpu-bar').style.width = `${gpu.utilization}%`;
        document.getElementById('gpu-label').textContent = `${gpu.utilization.toFixed(1)}%`;
      } else if (gpu.memory_percent !== undefined) {
        document.getElementById('gpu-bar').style.width = `${gpu.memory_percent}%`;
        document.getElementById('gpu-label').textContent = `${gpu.memory_percent.toFixed(1)}% mem`;
      }

      // Disk
      if (disk.percent !== undefined) {
        document.getElementById('disk-bar').style.width = `${disk.percent}%`;
        const used = (disk.used / 1024 / 1024 / 1024).toFixed(1);
        const total = (disk.total / 1024 / 1024 / 1024).toFixed(1);
        document.getElementById('disk-label').textContent = `${used} / ${total} GB`;
      }

      // Temperature
      if (temp.cpu !== undefined) {
        document.getElementById('temp-value').textContent = `${temp.cpu.toFixed(1)}°C`;
      } else if (temp.gpu !== undefined) {
        document.getElementById('temp-value').textContent = `${temp.gpu.toFixed(1)}°C`;
      }

    } catch (error) {
      console.error('Failed to load system stats:', error);
    }

    // Refresh system stats every 5 seconds when on system tab
    if (this.currentTab === 'system' && this.isConnected) {
      this.systemStatsTimeout = setTimeout(() => this.loadSystemStats(), 5000);
    }
  }

  async checkPTZ() {
    if (!this.isConnected) return;

    try {
      const response = await fetch(`${this.serverUrl}/api/ptz/status`);
      const data = await response.json();

      if (data.connected) {
        document.getElementById('ptz-controls').classList.remove('hidden');
      }
    } catch (error) {
      // PTZ not available
    }
  }

  async sendPTZCommand(direction) {
    if (!this.isConnected) return;

    try {
      if (direction === 'home') {
        await fetch(`${this.serverUrl}/api/ptz/home`, { method: 'POST' });
      } else {
        const movements = {
          up: { pan: 0, tilt: 0.5 },
          down: { pan: 0, tilt: -0.5 },
          left: { pan: -0.5, tilt: 0 },
          right: { pan: 0.5, tilt: 0 }
        };

        await fetch(`${this.serverUrl}/api/ptz/move`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(movements[direction])
        });

        // Stop after 500ms
        setTimeout(async () => {
          await fetch(`${this.serverUrl}/api/ptz/stop`, { method: 'POST' });
        }, 500);
      }
    } catch (error) {
      console.error('PTZ command failed:', error);
    }
  }

  async updateDetectionSettings(settings) {
    if (!this.isConnected) return;

    try {
      await fetch(`${this.serverUrl}/api/settings/detection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
    } catch (error) {
      console.error('Failed to update settings:', error);
    }
  }

  switchTab(tab) {
    this.currentTab = tab;

    // Clear system stats timeout if leaving system tab
    if (this.systemStatsTimeout) {
      clearTimeout(this.systemStatsTimeout);
      this.systemStatsTimeout = null;
    }

    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    // Update tab panels
    document.querySelectorAll('.tab-panel').forEach(panel => {
      panel.classList.toggle('active', panel.id === `tab-${tab}`);
    });

    // Load data for specific tabs
    if (tab === 'events') {
      this.loadEvents();
    } else if (tab === 'system') {
      this.loadSystemStats();
    }
  }

  openSettings() {
    document.getElementById('settings-modal').classList.remove('hidden');
  }

  closeSettings() {
    document.getElementById('settings-modal').classList.add('hidden');
  }

  disconnect() {
    this.isConnected = false;

    // Stop intervals
    if (this.statsInterval) {
      clearInterval(this.statsInterval);
      this.statsInterval = null;
    }
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
    if (this.systemStatsTimeout) {
      clearTimeout(this.systemStatsTimeout);
      this.systemStatsTimeout = null;
    }

    // Close WebSocket
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    // Stop stream
    document.getElementById('live-stream').src = '';

    // Switch screens
    document.getElementById('main-screen').classList.remove('active');
    document.getElementById('setup-screen').classList.add('active');

    // Close settings modal
    this.closeSettings();

    this.showStatus('Disconnected', '');
  }

  toggleFullscreen() {
    const container = document.querySelector('.stream-container');

    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      container.requestFullscreen().catch(err => {
        console.log('Fullscreen not supported');
      });
    }
  }

  showStatus(message, type) {
    const status = document.getElementById('connection-status');
    status.textContent = message;
    status.className = 'status';
    if (type) status.classList.add(type);
  }

  formatUptime(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }

  formatTime(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;

    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;

    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  formatEventType(type) {
    return type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  getClassIcon(className) {
    const icons = {
      person: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>',
      vehicle: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>',
      car: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>',
      truck: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>',
      package: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zm-7-2h2v-4h4v-2h-4V7h-2v4H8v2h4z"/></svg>'
    };
    return icons[className] || icons.person;
  }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  window.app = new CamaiApp();
});
