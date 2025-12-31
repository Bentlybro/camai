// CAMAI Mobile App
// Simple vanilla JS app that works in both browser and Capacitor WebView

// Import Capacitor plugins when available
let LocalNotifications = null;
let PushNotifications = null;
let BackgroundMode = null;
let App = null;
let camaiAppInstance = null;

// Initialize Capacitor plugins when DOM is ready
document.addEventListener('DOMContentLoaded', async () => {
  if (window.Capacitor && window.Capacitor.Plugins) {
    LocalNotifications = window.Capacitor.Plugins.LocalNotifications;
    PushNotifications = window.Capacitor.Plugins.PushNotifications;
    BackgroundMode = window.Capacitor.Plugins.BackgroundMode;
    App = window.Capacitor.Plugins.App;

    // Request notification permissions (local notifications as fallback)
    if (LocalNotifications) {
      try {
        const permResult = await LocalNotifications.requestPermissions();
        console.log('Local notification permission:', permResult.display);
      } catch (e) {
        console.log('Local notifications not available:', e);
      }
    }

    // Setup Firebase Push Notifications
    if (PushNotifications) {
      console.log('=== FCM DEBUG: PushNotifications plugin available ===');
      try {
        // Request permission
        console.log('FCM DEBUG: Requesting permissions...');
        const permResult = await PushNotifications.requestPermissions();
        console.log('FCM DEBUG: Permission result:', JSON.stringify(permResult));

        if (permResult.receive === 'granted') {
          // Register for push notifications
          console.log('FCM DEBUG: Permission granted, calling register()...');
          await PushNotifications.register();
          console.log('FCM DEBUG: register() called successfully');
        } else {
          console.log('FCM DEBUG: Permission NOT granted:', permResult.receive);
        }

        // Listen for registration success - get the FCM token
        PushNotifications.addListener('registration', (token) => {
          console.log('=== FCM DEBUG: GOT TOKEN ===');
          console.log('FCM Token:', token.value);
          console.log('Token length:', token.value?.length);
          // Store the token for later registration with server
          localStorage.setItem('fcm_token', token.value);

          // If app is already connected, register the token
          if (camaiAppInstance && camaiAppInstance.isConnected) {
            console.log('FCM DEBUG: App connected, registering token with server...');
            camaiAppInstance.registerFCMToken(token.value);
          } else {
            console.log('FCM DEBUG: App not connected yet, token saved for later');
          }
        });

        // Listen for registration errors
        PushNotifications.addListener('registrationError', (error) => {
          console.error('=== FCM DEBUG: REGISTRATION ERROR ===');
          console.error('FCM registration error:', JSON.stringify(error));
        });

        // Listen for push notifications received while app is in foreground
        PushNotifications.addListener('pushNotificationReceived', (notification) => {
          console.log('=== FCM DEBUG: PUSH RECEIVED (foreground) ===');
          console.log('Push notification received:', JSON.stringify(notification));
          // Vibrate on notification
          if (navigator.vibrate) {
            navigator.vibrate([200, 100, 200]);
          }
        });

        // Listen for push notification action (tap)
        PushNotifications.addListener('pushNotificationActionPerformed', (notification) => {
          console.log('=== FCM DEBUG: PUSH TAPPED ===');
          console.log('Push notification tapped:', JSON.stringify(notification));
          // App is brought to foreground, switch to live tab
          if (camaiAppInstance) {
            camaiAppInstance.switchTab('live');
          }
        });

      } catch (e) {
        console.error('=== FCM DEBUG: SETUP ERROR ===');
        console.error('Push notifications error:', e);
      }
    } else {
      console.log('=== FCM DEBUG: PushNotifications plugin NOT available ===');
    }

    // Setup background mode to keep WebSocket alive
    if (BackgroundMode) {
      try {
        // Configure background mode with foreground service
        await BackgroundMode.setSettings({
          title: 'CAMAI Monitoring',
          text: 'Watching for person detection',
          icon: 'ic_launcher',
          color: '00d4aa',
          resume: true,
          hidden: false,
          bigText: false,
          silent: false
        });

        // Handle app going to background
        BackgroundMode.addListener('appInBackground', () => {
          console.log('App went to background - keeping WebSocket alive');
        });

        // Handle app coming back to foreground - refresh stream and reconnect WebSocket if needed
        BackgroundMode.addListener('appInForeground', () => {
          console.log('App returned to foreground (BackgroundMode)');
          if (camaiAppInstance && camaiAppInstance.isConnected) {
            camaiAppInstance.handleAppResume();
          }
        });

        console.log('Background mode configured');
      } catch (e) {
        console.log('Background mode not available:', e);
      }
    }

    // Also listen for App state changes (Capacitor core) - only if BackgroundMode not available
    if (App && !BackgroundMode) {
      App.addListener('appStateChange', ({ isActive }) => {
        console.log('App state changed, isActive:', isActive);
        if (isActive && camaiAppInstance && camaiAppInstance.isConnected) {
          camaiAppInstance.handleAppResume();
        }
      });
    }
  }
});

class CamaiApp {
  constructor() {
    this.serverUrl = null;
    this.isConnected = false;
    this.ws = null;
    this.statsInterval = null;
    this.systemInterval = null;
    this.pingInterval = null;
    this.reconnectTimeout = null;
    this.reconnectAttempts = 0;
    this.maxReconnectDelay = 30000; // Max 30 seconds between attempts
    this.currentTab = 'live';
    this.showOverlays = true;
    this.eventsCache = [];
    this.recordingsCache = [];
    this.currentRecording = null;
    this.notificationId = 1;
    this.lastResumeTime = 0; // Debounce app resume
    this.isInBackground = false;

    // Store instance for background handlers
    camaiAppInstance = this;

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

    // PTZ auto-track toggle
    document.getElementById('ptz-auto-track').addEventListener('change', (e) => {
      this.toggleAutoTrack(e.target.checked);
    });

    // Detection settings toggles
    document.getElementById('setting-detect-person').addEventListener('change', (e) => {
      this.updateDisplaySettings({ detect_person: e.target.checked });
    });
    document.getElementById('setting-detect-vehicle').addEventListener('change', (e) => {
      this.updateDisplaySettings({ detect_vehicle: e.target.checked });
    });
    document.getElementById('setting-detect-package').addEventListener('change', (e) => {
      this.updateDisplaySettings({ detect_package: e.target.checked });
    });
    document.getElementById('setting-pose').addEventListener('change', (e) => {
      this.updatePoseSettings({ enabled: e.target.checked });
    });

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

    // Event modal
    document.getElementById('close-event-modal').addEventListener('click', () => this.closeEventModal());
    document.getElementById('event-modal').addEventListener('click', (e) => {
      if (e.target.id === 'event-modal') {
        this.closeEventModal();
      }
    });

    // Event snapshot loading
    document.getElementById('event-snapshot').addEventListener('load', () => {
      document.getElementById('event-snapshot').classList.add('loaded');
      document.getElementById('event-snapshot-loading').style.display = 'none';
      document.getElementById('event-snapshot-error').classList.add('hidden');
    });
    document.getElementById('event-snapshot').addEventListener('error', () => {
      document.getElementById('event-snapshot').classList.remove('loaded');
      document.getElementById('event-snapshot-loading').style.display = 'none';
      document.getElementById('event-snapshot-error').classList.remove('hidden');
    });

    // Recordings tab
    document.getElementById('recording-date-filter').addEventListener('change', (e) => {
      this.loadRecordings(e.target.value);
    });
    document.getElementById('refresh-recordings').addEventListener('click', () => {
      const dateFilter = document.getElementById('recording-date-filter').value;
      this.loadRecordings(dateFilter);
    });

    // Video modal
    document.getElementById('close-video-modal').addEventListener('click', () => this.closeVideoModal());
    document.getElementById('video-modal').addEventListener('click', (e) => {
      if (e.target.id === 'video-modal') {
        this.closeVideoModal();
      }
    });
    document.getElementById('delete-recording-btn').addEventListener('click', () => this.deleteRecording());

    // Person alert
    document.getElementById('close-alert').addEventListener('click', () => this.closePersonAlert());
    document.getElementById('view-live-btn').addEventListener('click', () => {
      this.closePersonAlert();
      this.switchTab('live');
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

    // Setup WebSocket for real-time updates (stats, detections, alerts)
    this.connectWebSocket();

    // Enable background mode to keep WebSocket alive when app is minimized
    this.enableBackgroundMode();

    // Stats and detections come via WebSocket - no polling needed!
    // Only poll system stats (CPU, memory, etc.) every 30 seconds
    this.systemInterval = setInterval(() => this.loadSystemStats(), 30000);

    // Initial loads for data not sent via WebSocket
    this.loadEvents();
    this.loadSystemStats();
    this.checkPTZ();
    this.loadServerSettings();

    // Register FCM token for push notifications
    this.registerFCMToken();

    // Update server display
    document.getElementById('setting-server').textContent = this.serverUrl;
  }

  handleAppResume() {
    // Debounce - ignore if called within 2 seconds of last resume
    const now = Date.now();
    if (now - this.lastResumeTime < 2000) {
      console.log('Ignoring duplicate resume event');
      return;
    }
    this.lastResumeTime = now;

    console.log('Handling app resume...');

    // Refresh the stream - it dies when app goes to background
    this.updateStream();

    // Check WebSocket state and reconnect if needed
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.log('WebSocket not open, reconnecting...');
      this.connectWebSocket();
    }
  }

  async registerFCMToken(token = null) {
    // Register FCM token with server for push notifications
    console.log('=== FCM DEBUG: registerFCMToken called ===');
    try {
      // Get token from parameter or localStorage
      const fcmToken = token || localStorage.getItem('fcm_token');
      console.log('FCM DEBUG: Token from param:', token ? 'yes' : 'no');
      console.log('FCM DEBUG: Token from storage:', localStorage.getItem('fcm_token') ? 'yes' : 'no');

      if (!fcmToken) {
        console.log('FCM DEBUG: No FCM token available yet');
        return;
      }

      console.log('FCM DEBUG: Token length:', fcmToken.length);
      console.log('FCM DEBUG: Token preview:', fcmToken.substring(0, 30) + '...');

      // Get device name
      const deviceName = navigator.userAgent.includes('Android') ? 'Android Phone' : 'Mobile Device';
      const url = `${this.serverUrl}/api/notifications/register`;
      console.log('FCM DEBUG: Registering with server:', url);

      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: fcmToken,
          device_name: deviceName,
          platform: 'android'
        })
      });

      console.log('FCM DEBUG: Server response status:', response.status);

      if (response.ok) {
        const data = await response.json();
        console.log('=== FCM DEBUG: TOKEN REGISTERED SUCCESS ===');
        console.log('FCM token registered:', data.message);
      } else {
        const errorText = await response.text();
        console.error('=== FCM DEBUG: TOKEN REGISTRATION FAILED ===');
        console.error('Failed to register FCM token:', response.status, errorText);
      }
    } catch (error) {
      console.error('=== FCM DEBUG: TOKEN REGISTRATION ERROR ===');
      console.error('Error registering FCM token:', error);
    }
  }

  updateStream() {
    const stream = document.getElementById('live-stream');
    const endpoint = this.showOverlays ? '/stream' : '/clean-stream';
    // Add timestamp to force refresh and break cache
    stream.src = `${this.serverUrl}${endpoint}?t=${Date.now()}`;

    // Set up error handler to auto-retry on stream failure
    stream.onerror = () => {
      console.log('Stream error, retrying in 2 seconds...');
      setTimeout(() => {
        if (this.isConnected) {
          stream.src = `${this.serverUrl}${endpoint}?t=${Date.now()}`;
        }
      }, 2000);
    };
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
        this.reconnectAttempts = 0; // Reset on successful connection
        this.updateConnectionStatus(true);
      };

      this.ws.onclose = (event) => {
        console.log('WebSocket disconnected, code:', event.code, 'reason:', event.reason);
        this.updateConnectionStatus(false);

        // Clear any existing reconnect timeout
        if (this.reconnectTimeout) {
          clearTimeout(this.reconnectTimeout);
          this.reconnectTimeout = null;
        }

        // Auto-reconnect with exponential backoff if we're supposed to be connected
        if (this.isConnected) {
          this.reconnectAttempts++;
          // Exponential backoff: 3s, 6s, 12s, 24s, max 30s
          const delay = Math.min(3000 * Math.pow(2, this.reconnectAttempts - 1), this.maxReconnectDelay);
          console.log(`Auto-reconnecting in ${delay/1000}s (attempt ${this.reconnectAttempts})...`);

          this.reconnectTimeout = setTimeout(() => {
            if (this.isConnected) {
              console.log('Attempting WebSocket reconnection...');
              this.connectWebSocket();
            }
          }, delay);
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          switch (data.type) {
            case 'stats':
              this.updateLiveStats(data.data || data);
              break;
            case 'detections':
              this.renderLiveDetections(data.data?.detections || data.detections || []);
              break;
            case 'person_alert':
              // Always send notification (works in foreground and background)
              this.showPersonAlert(data);
              break;
            case 'pong':
              // Ping response, connection is alive
              break;
          }
        } catch (e) {
          // Ignore parse errors
        }
      };

      // Clear any existing ping interval
      if (this.pingInterval) clearInterval(this.pingInterval);

      // Send ping every 15 seconds to keep connection alive in background
      this.pingInterval = setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        } else if (this.isConnected) {
          // WebSocket disconnected but we think we're connected - reconnect
          console.log('WebSocket dead, reconnecting...');
          this.connectWebSocket();
        }
      }, 15000);

    } catch (e) {
      console.error('WebSocket setup error:', e);
    }
  }

  updateConnectionStatus(connected) {
    const dot = document.querySelector('.connection-dot');
    const status = document.getElementById('header-status');

    if (connected) {
      dot.classList.remove('disconnected');
      dot.classList.remove('reconnecting');
      status.textContent = 'Connected';
    } else if (this.isConnected) {
      // We want to be connected but aren't - show reconnecting
      dot.classList.add('disconnected');
      dot.classList.add('reconnecting');
      status.textContent = this.reconnectAttempts > 0
        ? `Reconnecting (${this.reconnectAttempts})...`
        : 'Reconnecting...';
    } else {
      // Intentionally disconnected
      dot.classList.add('disconnected');
      dot.classList.remove('reconnecting');
      status.textContent = 'Disconnected';
    }
  }

  async loadStats() {
    if (!this.isConnected) return;

    try {
      // Use full stats endpoint for detailed breakdown
      const response = await fetch(`${this.serverUrl}/api/stats`);
      const data = await response.json();

      // Update stats tab from summary
      const summary = data.summary || {};
      document.getElementById('stat-persons').textContent = summary.person_events || 0;
      document.getElementById('stat-vehicles').textContent = summary.vehicle_events || 0;
      document.getElementById('stat-packages').textContent = summary.package_events || 0;
      document.getElementById('stat-total').textContent = summary.total_events_today || 0;

      // Update uptime (already formatted from API)
      const system = data.system || {};
      if (system.uptime_formatted) {
        document.getElementById('stat-uptime').textContent = system.uptime_formatted;
      }

      // Update performance
      document.getElementById('perf-fps').textContent = (system.fps || 0).toFixed(1);
      document.getElementById('perf-inference').textContent = `${(system.inference_ms || 0).toFixed(1)} ms`;
      document.getElementById('perf-tracked').textContent = system.tracked_objects || 0;

      // Update stream overlay
      document.getElementById('stream-fps').textContent = `${(system.fps || 0).toFixed(1)} FPS`;
      document.getElementById('stream-latency').textContent = `${(system.inference_ms || 0).toFixed(1)} ms`;

      // Update live stats bar
      document.getElementById('live-fps').textContent = (system.fps || 0).toFixed(1);
      document.getElementById('live-inference').textContent = (system.inference_ms || 0).toFixed(1);
      document.getElementById('live-frames').textContent = this.formatNumber(system.frame_count || 0);
      document.getElementById('live-tracked').textContent = system.tracked_objects || 0;
      document.getElementById('live-uptime').textContent = system.uptime_formatted || '--';

      // Load current detections
      await this.loadDetections();

    } catch (error) {
      console.error('Failed to load stats:', error);
    }
  }

  updateLiveStats(data) {
    // Update FPS
    if (data.fps !== undefined) {
      const fpsVal = parseFloat(data.fps).toFixed(1);
      document.getElementById('stream-fps').textContent = `${fpsVal} FPS`;
      document.getElementById('live-fps').textContent = fpsVal;
      document.getElementById('perf-fps').textContent = fpsVal;
    }

    // Update inference time
    const inferenceTime = data.inference_time || data.inference_ms;
    if (inferenceTime !== undefined) {
      const msVal = parseFloat(inferenceTime).toFixed(1);
      document.getElementById('stream-latency').textContent = `${msVal} ms`;
      document.getElementById('live-inference').textContent = msVal;
      document.getElementById('perf-inference').textContent = `${msVal} ms`;
    }

    // Update frame count
    if (data.frame_count !== undefined) {
      document.getElementById('live-frames').textContent = this.formatNumber(data.frame_count);
    }

    // Update tracked objects
    if (data.tracked_objects !== undefined) {
      document.getElementById('live-tracked').textContent = data.tracked_objects;
      document.getElementById('perf-tracked').textContent = data.tracked_objects;
    }

    // Update uptime
    if (data.uptime_formatted) {
      document.getElementById('live-uptime').textContent = data.uptime_formatted;
      document.getElementById('stat-uptime').textContent = data.uptime_formatted;
    }
  }

  renderLiveDetections(detections) {
    const container = document.getElementById('detections-list');

    if (!detections || detections.length === 0) {
      container.innerHTML = '<p class="empty-state">No active detections</p>';
      return;
    }

    container.innerHTML = detections.map(det => {
      const cls = det.class || 'object';
      const conf = ((det.confidence || 0) * 100).toFixed(0);
      const icon = cls === 'person' ? '👤' : cls.includes('car') || cls.includes('vehicle') || cls.includes('truck') ? '🚗' : '📦';
      const status = det.stationary_time > 5 ? 'stopped' : det.stationary_time > 30 ? 'parked' : '';

      return `
        <div class="detection-item ${status ? 'status-' + status : ''}">
          <div class="detection-icon ${cls === 'person' ? 'person' : cls.includes('car') || cls.includes('vehicle') ? 'vehicle' : 'package'}">
            ${icon}
          </div>
          <div class="detection-info">
            <div class="detection-class">${cls}</div>
            <div class="detection-meta">
              <span class="detection-conf">${conf}%</span>
              ${status ? `<span class="detection-status">${status}</span>` : ''}
            </div>
          </div>
        </div>
      `;
    }).join('');
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

      container.innerHTML = data.detections.map(det => {
        const className = det.class || 'unknown';
        const displayName = det.description || className;
        const status = det.status ? ` (${det.status})` : '';
        return `
          <div class="detection-item ${className}">
            <div class="detection-icon ${className}">
              ${this.getClassIcon(className)}
            </div>
            <div class="detection-info">
              <div class="detection-type">${displayName}${status}</div>
              <div class="detection-conf">${(det.confidence * 100).toFixed(0)}% confidence</div>
            </div>
          </div>
        `;
      }).join('');

    } catch (error) {
      console.error('Failed to load detections:', error);
    }
  }

  async loadEvents() {
    if (!this.isConnected) return;

    try {
      const filter = document.getElementById('event-filter').value;
      let url = `${this.serverUrl}/api/events?limit=50`;
      if (filter) url += `&event_type=${filter}`;

      const response = await fetch(url);
      const events = await response.json();  // API returns array directly

      // Store events for detail view
      this.eventsCache = events;

      const container = document.getElementById('events-list');

      if (!events || events.length === 0) {
        container.innerHTML = '<p class="empty-state">No events found</p>';
        return;
      }

      container.innerHTML = events.map(event => {
        // Extract class from event type (e.g., "person_detected" -> "person")
        const eventClass = this.extractClassFromType(event.type);
        return `
          <div class="event-item" data-id="${event.id}">
            <span class="event-type-badge ${eventClass}">${eventClass}</span>
            <div class="event-details">
              <div class="event-title">${this.formatEventType(event.type)}</div>
              <div class="event-time">${this.formatTime(event.timestamp)}</div>
            </div>
            <span class="event-confidence">${(event.confidence * 100).toFixed(0)}%</span>
          </div>
        `;
      }).join('');

      // Add click handlers to event items
      container.querySelectorAll('.event-item').forEach(item => {
        item.addEventListener('click', () => {
          const eventId = parseInt(item.dataset.id);
          this.showEventDetail(eventId);
        });
      });

    } catch (error) {
      console.error('Failed to load events:', error);
      document.getElementById('events-list').innerHTML = '<p class="empty-state">Failed to load events</p>';
    }
  }

  async loadSystemStats() {
    if (!this.isConnected) return;

    try {
      // Use single endpoint for all system stats
      const response = await fetch(`${this.serverUrl}/api/system`);
      const data = await response.json();

      // CPU
      if (data.cpu) {
        const cpuPercent = data.cpu.usage_percent || 0;
        document.getElementById('cpu-bar').style.width = `${cpuPercent}%`;
        document.getElementById('cpu-label').textContent = `${cpuPercent.toFixed(1)}%`;
      }

      // Memory
      if (data.memory) {
        const memPercent = data.memory.usage_percent || 0;
        document.getElementById('memory-bar').style.width = `${memPercent}%`;
        const used = data.memory.used_gb || (data.memory.used_bytes / 1024 / 1024 / 1024);
        const total = data.memory.total_gb || (data.memory.total_bytes / 1024 / 1024 / 1024);
        document.getElementById('memory-label').textContent = `${used.toFixed(1)} / ${total.toFixed(1)} GB`;
      }

      // GPU
      if (data.gpu) {
        const gpuPercent = data.gpu.usage_percent || 0;
        document.getElementById('gpu-bar').style.width = `${gpuPercent}%`;
        document.getElementById('gpu-label').textContent = `${gpuPercent.toFixed(1)}%`;
      }

      // Disk
      if (data.disk) {
        const diskPercent = data.disk.usage_percent || 0;
        document.getElementById('disk-bar').style.width = `${diskPercent}%`;
        const used = data.disk.used_gb || (data.disk.used_bytes / 1024 / 1024 / 1024);
        const total = data.disk.total_gb || (data.disk.total_bytes / 1024 / 1024 / 1024);
        document.getElementById('disk-label').textContent = `${used.toFixed(1)} / ${total.toFixed(1)} GB`;
      }

      // Temperature - get max or average
      if (data.temperature) {
        const temp = data.temperature._max || data.temperature._avg ||
                     data.temperature.CPU || data.temperature.GPU || 0;
        document.getElementById('temp-value').textContent = `${temp.toFixed(1)}°C`;
      }

    } catch (error) {
      console.error('Failed to load system stats:', error);
    }
  }

  async checkPTZ() {
    if (!this.isConnected) return;

    try {
      const response = await fetch(`${this.serverUrl}/api/ptz/status`);
      const data = await response.json();

      if (data.connected) {
        document.getElementById('ptz-controls').classList.remove('hidden');

        // Set auto-track checkbox state
        const autoTrackCheckbox = document.getElementById('ptz-auto-track');
        if (autoTrackCheckbox) {
          autoTrackCheckbox.checked = data.auto_tracking || false;
        }

        // Load presets
        await this.loadPTZPresets();
      }
    } catch (error) {
      // PTZ not available
    }
  }

  async loadPTZPresets() {
    try {
      const response = await fetch(`${this.serverUrl}/api/ptz/presets`);
      const presets = await response.json();

      const container = document.getElementById('ptz-presets');
      if (!container || !presets || presets.length === 0) return;

      container.innerHTML = presets.map(preset => `
        <button class="ptz-preset-btn" data-token="${preset.token}">
          ${preset.name}
        </button>
      `).join('');

      // Add click handlers
      container.querySelectorAll('.ptz-preset-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const token = e.target.dataset.token;
          this.goToPreset(token);
        });
      });
    } catch (error) {
      console.error('Failed to load PTZ presets:', error);
    }
  }

  async goToPreset(token) {
    if (!this.isConnected) return;

    try {
      await fetch(`${this.serverUrl}/api/ptz/presets/${token}/goto`, { method: 'POST' });
    } catch (error) {
      console.error('Failed to go to preset:', error);
    }
  }

  async toggleAutoTrack(enabled) {
    if (!this.isConnected) return;

    try {
      await fetch(`${this.serverUrl}/api/ptz/auto-track`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
      });
    } catch (error) {
      console.error('Failed to toggle auto-track:', error);
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

  async updateDisplaySettings(settings) {
    if (!this.isConnected) return;

    try {
      await fetch(`${this.serverUrl}/api/settings/display`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
    } catch (error) {
      console.error('Failed to update display settings:', error);
    }
  }

  async updatePoseSettings(settings) {
    if (!this.isConnected) return;

    try {
      await fetch(`${this.serverUrl}/api/settings/pose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
    } catch (error) {
      console.error('Failed to update pose settings:', error);
    }
  }

  async loadServerSettings() {
    if (!this.isConnected) return;

    try {
      const response = await fetch(`${this.serverUrl}/api/settings`);
      const settings = await response.json();

      // Apply detection settings
      if (settings.detection) {
        const confSlider = document.getElementById('setting-confidence');
        const confValue = document.getElementById('confidence-value');
        if (confSlider && settings.detection.confidence) {
          confSlider.value = settings.detection.confidence;
          confValue.textContent = settings.detection.confidence;
        }
      }

      // Apply display settings
      if (settings.display) {
        const overlaysCheckbox = document.getElementById('setting-overlays');
        const personCheckbox = document.getElementById('setting-detect-person');
        const vehicleCheckbox = document.getElementById('setting-detect-vehicle');
        const packageCheckbox = document.getElementById('setting-detect-package');

        if (overlaysCheckbox) overlaysCheckbox.checked = settings.display.show_overlays !== false;
        if (personCheckbox) personCheckbox.checked = settings.display.detect_person !== false;
        if (vehicleCheckbox) vehicleCheckbox.checked = settings.display.detect_vehicle !== false;
        if (packageCheckbox) packageCheckbox.checked = settings.display.detect_package === true;

        // Update local overlay setting
        this.showOverlays = settings.display.show_overlays !== false;
      }

      // Apply pose settings
      if (settings.pose) {
        const poseCheckbox = document.getElementById('setting-pose');
        if (poseCheckbox) poseCheckbox.checked = settings.pose.enabled === true;
      }

    } catch (error) {
      console.error('Failed to load server settings:', error);
    }
  }

  switchTab(tab) {
    this.currentTab = tab;

    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    // Update tab panels
    document.querySelectorAll('.tab-panel').forEach(panel => {
      panel.classList.toggle('active', panel.id === `tab-${tab}`);
    });

    // Load fresh data when switching to specific tabs
    if (tab === 'events') {
      this.loadEvents();
    } else if (tab === 'system') {
      this.loadSystemStats();
    } else if (tab === 'stats') {
      this.loadStats();
    } else if (tab === 'recordings') {
      this.loadRecordings();
      this.loadRecordingStats();
    }
  }

  openSettings() {
    document.getElementById('settings-modal').classList.remove('hidden');
  }

  closeSettings() {
    document.getElementById('settings-modal').classList.add('hidden');
  }

  showEventDetail(eventId) {
    const event = this.eventsCache.find(e => e.id === eventId);
    if (!event) return;

    const eventClass = this.extractClassFromType(event.type);

    // Reset snapshot state
    const snapshot = document.getElementById('event-snapshot');
    snapshot.classList.remove('loaded');
    document.getElementById('event-snapshot-loading').style.display = 'block';
    document.getElementById('event-snapshot-error').classList.add('hidden');

    // Set snapshot image
    if (event.snapshot_path) {
      snapshot.src = `${this.serverUrl}${event.snapshot_path}`;
    } else {
      snapshot.src = '';
      document.getElementById('event-snapshot-loading').style.display = 'none';
      document.getElementById('event-snapshot-error').classList.remove('hidden');
    }

    // Set event info
    document.getElementById('event-modal-title').textContent = this.formatEventType(event.type);

    const typeEl = document.getElementById('event-info-type');
    typeEl.textContent = eventClass;
    typeEl.className = `event-info-value type-badge ${eventClass}`;

    document.getElementById('event-info-confidence').textContent = `${(event.confidence * 100).toFixed(0)}%`;
    document.getElementById('event-info-time').textContent = this.formatFullTime(event.timestamp);

    // Description (hide if empty)
    const descRow = document.getElementById('event-info-description-row');
    const descEl = document.getElementById('event-info-description');
    if (event.description) {
      descEl.textContent = event.description;
      descRow.style.display = 'flex';
    } else {
      descRow.style.display = 'none';
    }

    // Color (hide if empty)
    const colorRow = document.getElementById('event-info-color-row');
    const colorEl = document.getElementById('event-info-color');
    if (event.color) {
      colorEl.textContent = event.color;
      colorRow.style.display = 'flex';
    } else {
      colorRow.style.display = 'none';
    }

    // Show modal
    document.getElementById('event-modal').classList.remove('hidden');
  }

  closeEventModal() {
    document.getElementById('event-modal').classList.add('hidden');
    document.getElementById('event-snapshot').src = '';
  }

  // === RECORDINGS ===

  async loadRecordings(date = null) {
    if (!this.isConnected) return;

    try {
      let url = `${this.serverUrl}/api/recordings?limit=50`;
      if (date) url += `&date=${date}`;

      const response = await fetch(url);
      const data = await response.json();

      this.recordingsCache = data.recordings || [];

      const container = document.getElementById('recordings-list');

      if (!this.recordingsCache || this.recordingsCache.length === 0) {
        container.innerHTML = '<p class="empty-state">No recordings found</p>';
        return;
      }

      container.innerHTML = this.recordingsCache.map(rec => `
        <div class="recording-item" data-id="${rec.id}">
          <div class="recording-thumbnail">
            ${rec.thumbnail_path
              ? `<img src="${this.serverUrl}/api/recordings/${rec.id}/thumbnail" alt="Thumbnail">`
              : `<svg viewBox="0 0 24 24" width="24" height="24"><path fill="currentColor" d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>`
            }
          </div>
          <div class="recording-info">
            <div class="recording-date">${rec.formatted_time || this.formatTime(rec.start_time)}</div>
            <div class="recording-meta">
              <span class="recording-duration">${rec.formatted_duration || '--:--'}</span>
              <span>${rec.formatted_size || '--'}</span>
            </div>
          </div>
        </div>
      `).join('');

      // Add click handlers
      container.querySelectorAll('.recording-item').forEach(item => {
        item.addEventListener('click', () => {
          const recordingId = parseInt(item.dataset.id);
          this.showRecording(recordingId);
        });
      });

    } catch (error) {
      console.error('Failed to load recordings:', error);
      document.getElementById('recordings-list').innerHTML = '<p class="empty-state">Failed to load recordings</p>';
    }
  }

  async loadRecordingStats() {
    if (!this.isConnected) return;

    try {
      const response = await fetch(`${this.serverUrl}/api/recordings/stats`);
      const stats = await response.json();

      document.getElementById('recordings-count').textContent = `${stats.total_recordings || 0} recordings`;
      document.getElementById('recordings-storage').textContent = stats.formatted_size || '0 GB used';

    } catch (error) {
      console.error('Failed to load recording stats:', error);
    }
  }

  showRecording(recordingId) {
    const recording = this.recordingsCache.find(r => r.id === recordingId);
    if (!recording) return;

    this.currentRecording = recording;

    // Set video source
    const video = document.getElementById('recording-video');
    const fallback = document.getElementById('video-fallback');

    // Reset fallback state
    fallback.classList.add('hidden');
    video.style.display = 'block';

    video.src = `${this.serverUrl}/api/recordings/${recordingId}/stream`;

    // Handle video error (codec not supported)
    video.onerror = () => {
      console.log('Video playback error - showing fallback');
      video.style.display = 'none';
      fallback.classList.remove('hidden');
    };

    // Set download link
    const downloadBtn = document.getElementById('download-recording-btn');
    downloadBtn.href = `${this.serverUrl}/api/recordings/${recordingId}/download`;
    downloadBtn.download = recording.filename || `recording_${recordingId}.mp4`;

    // Set info
    document.getElementById('video-modal-title').textContent = recording.filename || 'Recording';
    document.getElementById('video-info-date').textContent = recording.formatted_time || this.formatFullTime(recording.start_time);
    document.getElementById('video-info-duration').textContent = recording.formatted_duration || '--:--';
    document.getElementById('video-info-size').textContent = recording.formatted_size || '--';

    // Show modal
    document.getElementById('video-modal').classList.remove('hidden');
  }

  closeVideoModal() {
    const video = document.getElementById('recording-video');
    video.pause();
    video.src = '';
    video.onerror = null;
    video.style.display = 'block';
    document.getElementById('video-fallback').classList.add('hidden');
    document.getElementById('video-modal').classList.add('hidden');
    this.currentRecording = null;
  }

  async deleteRecording() {
    if (!this.currentRecording) return;

    if (!confirm('Are you sure you want to delete this recording?')) return;

    try {
      const response = await fetch(`${this.serverUrl}/api/recordings/${this.currentRecording.id}`, {
        method: 'DELETE'
      });

      if (response.ok) {
        this.closeVideoModal();
        // Refresh recordings list
        const dateFilter = document.getElementById('recording-date-filter').value;
        this.loadRecordings(dateFilter);
        this.loadRecordingStats();
      } else {
        alert('Failed to delete recording');
      }
    } catch (error) {
      console.error('Failed to delete recording:', error);
      alert('Failed to delete recording');
    }
  }

  // === PERSON ALERT ===

  showPersonAlert(data) {
    // Person alert received via WebSocket
    // Firebase handles the actual push notification - we just vibrate here for in-app feedback
    console.log('Person alert received via WebSocket');

    // Vibrate if supported (in-app haptic feedback)
    if (navigator.vibrate) {
      navigator.vibrate([200, 100, 200]);
    }

    // Note: Push notifications are handled by Firebase Cloud Messaging
    // Local notifications are kept as fallback if FCM isn't working
    if (!localStorage.getItem('fcm_token')) {
      // No FCM token - use local notification as fallback
      const detections = data.detections || [];
      const personCount = detections.filter(d => d.class === 'person').length;
      const alertText = personCount > 1 ? `${personCount} people detected` : '1 person detected';
      const timestamp = data.timestamp ? data.timestamp * 1000 : Date.now();
      const screenshotBase64 = data.screenshot || null;
      this.sendLocalNotification('Person Detected', alertText, timestamp, screenshotBase64);
    }
  }

  async sendLocalNotification(title, body, timestamp, screenshotBase64 = null) {
    if (!LocalNotifications) {
      console.log('Local notifications not available');
      return;
    }

    try {
      // Create notification channel first (required for Android 8+)
      try {
        await LocalNotifications.createChannel({
          id: 'person_alerts',
          name: 'Person Detection Alerts',
          description: 'Notifications when a person is detected',
          importance: 5, // IMPORTANCE_HIGH
          visibility: 1, // VISIBILITY_PUBLIC
          sound: 'default',
          vibration: true,
          lights: true
        });
      } catch (channelErr) {
        // Channel might already exist, that's ok
      }

      // Build notification config
      const notificationConfig = {
        title: title,
        body: body,
        id: this.notificationId++,
        sound: 'default',
        smallIcon: 'ic_stat_icon_config_sample',
        iconColor: '#00d4aa',
        channelId: 'person_alerts',
        autoCancel: true,
        extra: {
          type: 'person_alert',
          timestamp: timestamp
        }
      };

      // Add screenshot as large icon and big picture style if available
      if (screenshotBase64) {
        // Use base64 image for largeIcon (thumbnail in notification)
        notificationConfig.largeIcon = `data:image/jpeg;base64,${screenshotBase64}`;
        // Use attachments for expanded big picture notification
        notificationConfig.attachments = [
          {
            id: 'screenshot',
            url: `data:image/jpeg;base64,${screenshotBase64}`
          }
        ];
        // Enable big picture style on Android
        notificationConfig.largeBody = body;
        notificationConfig.summaryText = 'Tap to view live stream';
      }

      // Fire notification immediately
      await LocalNotifications.schedule({
        notifications: [notificationConfig]
      });
      console.log('Local notification sent with screenshot');
    } catch (err) {
      console.error('Failed to send local notification:', err);
    }
  }

  closePersonAlert() {
    document.getElementById('person-alert').classList.add('hidden');
    // Stop the live stream to save bandwidth
    document.getElementById('alert-live-stream').src = '';
  }

  async enableBackgroundMode() {
    if (!BackgroundMode) {
      console.log('Background mode not available');
      return;
    }

    try {
      // Request to disable battery optimization (important for background execution)
      try {
        await BackgroundMode.disableBatteryOptimizations();
        console.log('Battery optimization disabled');
      } catch (battErr) {
        console.log('Battery optimization request:', battErr.message || 'handled');
      }

      // Request to disable web view optimizations
      try {
        await BackgroundMode.disableWebViewOptimizations();
        console.log('WebView optimizations disabled');
      } catch (webErr) {
        console.log('WebView optimization request:', webErr.message || 'handled');
      }

      // Enable background mode (starts foreground service)
      await BackgroundMode.enable();
      console.log('Background mode enabled - WebSocket will stay alive when app is minimized');
    } catch (err) {
      console.error('Failed to enable background mode:', err);
    }
  }

  async disableBackgroundMode() {
    if (!BackgroundMode) return;

    try {
      await BackgroundMode.disable();
      console.log('Background mode disabled');
    } catch (err) {
      console.error('Failed to disable background mode:', err);
    }
  }

  formatFullTime(timestamp) {
    const ts = timestamp > 1e12 ? timestamp : timestamp * 1000;
    const date = new Date(ts);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
  }

  disconnect() {
    this.isConnected = false;

    // Stop intervals
    if (this.statsInterval) {
      clearInterval(this.statsInterval);
      this.statsInterval = null;
    }
    if (this.systemInterval) {
      clearInterval(this.systemInterval);
      this.systemInterval = null;
    }
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
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

  formatNumber(num) {
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
  }

  formatTime(timestamp) {
    // Handle Unix timestamps (seconds) - convert to milliseconds
    const ts = timestamp > 1e12 ? timestamp : timestamp * 1000;
    const date = new Date(ts);
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

  extractClassFromType(type) {
    // Extract class from event type (e.g., "person_detected" -> "person", "vehicle_left" -> "vehicle")
    if (!type) return 'unknown';
    const lower = type.toLowerCase();
    if (lower.includes('person')) return 'person';
    if (lower.includes('vehicle') || lower.includes('car') || lower.includes('truck')) return 'vehicle';
    if (lower.includes('package')) return 'package';
    return 'unknown';
  }

  getClassIcon(className) {
    const icons = {
      person: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>',
      vehicle: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>',
      car: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>',
      truck: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>',
      package: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zm-7-2h2v-4h4v-2h-4V7h-2v4H8v2h4z"/></svg>'
    };
    // Normalize class name - treat "car" as "vehicle" for styling
    const normalizedClass = className === 'car' || className === 'truck' ? 'vehicle' : className;
    return icons[className] || icons[normalizedClass] || icons.person;
  }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  window.app = new CamaiApp();
});
