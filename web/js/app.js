// CAMAI Dashboard JavaScript

class CAMAIDashboard {
    constructor() {
        this.ws = null;
        this.reconnectInterval = 5000;
        this.statsInterval = null;

        this.init();
    }

    init() {
        this.setupNavigation();
        this.setupWebSocket();
        this.setupControls();
        this.setupPTZControls();
        this.loadSettings();
        this.loadEvents();
        this.loadSnapshots();
        this.startStatsPolling();
        this.checkPTZStatus();
    }

    // Navigation
    setupNavigation() {
        document.querySelectorAll('.nav-link, .view-all').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const page = e.target.dataset.page;
                if (page) this.showPage(page);
            });
        });
    }

    showPage(pageName) {
        // Update nav
        document.querySelectorAll('.nav-link').forEach(link => {
            link.classList.toggle('active', link.dataset.page === pageName);
        });

        // Update pages
        document.querySelectorAll('.page').forEach(page => {
            page.classList.toggle('active', page.id === `page-${pageName}`);
        });

        // Load page-specific data
        if (pageName === 'events') {
            this.loadEvents();
            this.loadSnapshots();
        } else if (pageName === 'settings') {
            this.loadSettings();
        }
    }

    // WebSocket
    setupWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('WebSocket connected');
                this.updateConnectionStatus(true);
            };

            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleWSMessage(data);
            };

            this.ws.onclose = () => {
                console.log('WebSocket disconnected');
                this.updateConnectionStatus(false);
                setTimeout(() => this.setupWebSocket(), this.reconnectInterval);
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (e) {
            console.error('WebSocket setup failed:', e);
            setTimeout(() => this.setupWebSocket(), this.reconnectInterval);
        }
    }

    handleWSMessage(data) {
        switch (data.type) {
            case 'stats':
                this.updateStats(data.data);
                break;
            case 'event':
                this.addEvent(data.data);
                break;
            case 'ping':
                this.ws.send(JSON.stringify({ type: 'pong' }));
                break;
        }
    }

    updateConnectionStatus(online) {
        const dot = document.querySelector('.status-dot');
        const text = document.getElementById('status-text');

        if (online) {
            dot.classList.add('online');
            text.textContent = 'Connected';
        } else {
            dot.classList.remove('online');
            text.textContent = 'Disconnected';
        }
    }

    // Stats
    startStatsPolling() {
        this.fetchStats();
        this.statsInterval = setInterval(() => this.fetchStats(), 2000);
    }

    async fetchStats() {
        try {
            const response = await fetch('/api/stats');
            const stats = await response.json();
            this.updateStats(stats);
        } catch (e) {
            console.error('Failed to fetch stats:', e);
        }
    }

    updateStats(stats) {
        document.getElementById('stat-fps').textContent = stats.fps || '--';
        document.getElementById('stat-inference').textContent = stats.inference_ms || '--';
        document.getElementById('stat-frames').textContent = this.formatNumber(stats.frame_count) || '--';
        document.getElementById('stat-tracked').textContent = stats.tracked_objects || '--';
        document.getElementById('stat-uptime').textContent = stats.uptime || '--';

        document.getElementById('fps-badge').textContent = `${stats.fps || '--'} FPS`;
    }

    formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num;
    }

    // PTZ Controls
    setupPTZControls() {
        // Direction buttons - hold to move, release to stop
        const ptzButtons = document.querySelectorAll('.ptz-btn[data-pan]');
        ptzButtons.forEach(btn => {
            btn.addEventListener('mousedown', (e) => {
                const pan = parseFloat(btn.dataset.pan);
                const tilt = parseFloat(btn.dataset.tilt);
                this.ptzMove(pan, tilt);
            });
            btn.addEventListener('mouseup', () => this.ptzStop());
            btn.addEventListener('mouseleave', () => this.ptzStop());

            // Touch support
            btn.addEventListener('touchstart', (e) => {
                e.preventDefault();
                const pan = parseFloat(btn.dataset.pan);
                const tilt = parseFloat(btn.dataset.tilt);
                this.ptzMove(pan, tilt);
            });
            btn.addEventListener('touchend', () => this.ptzStop());
        });

        // Stop button
        document.getElementById('ptz-stop')?.addEventListener('click', () => {
            this.ptzStop();
        });
    }

    async ptzMove(pan, tilt) {
        try {
            await fetch('/api/ptz/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pan, tilt })
            });
            // Update toggle to show auto-tracking is now off
            const toggle = document.getElementById('toggle-ptz');
            if (toggle) toggle.checked = false;
        } catch (e) {
            console.error('PTZ move failed:', e);
        }
    }

    async ptzStop() {
        try {
            await fetch('/api/ptz/stop', { method: 'POST' });
        } catch (e) {
            console.error('PTZ stop failed:', e);
        }
    }

    async checkPTZStatus() {
        try {
            const response = await fetch('/api/ptz/status');
            const status = await response.json();

            const badge = document.getElementById('ptz-status');
            if (badge) {
                if (status.connected) {
                    badge.textContent = 'Connected';
                    badge.classList.add('ptz-status-connected');
                    badge.classList.remove('badge-secondary');
                } else {
                    badge.textContent = 'Disconnected';
                    badge.classList.remove('ptz-status-connected');
                    badge.classList.add('badge-secondary');
                }
            }
        } catch (e) {
            console.error('Failed to check PTZ status:', e);
        }
    }

    // Controls
    setupControls() {
        // Quick toggles on dashboard
        document.getElementById('toggle-ptz')?.addEventListener('change', (e) => {
            this.updateSetting('ptz', { enabled: e.target.checked });
        });

        document.getElementById('toggle-pose')?.addEventListener('change', (e) => {
            this.updateSetting('pose', { enabled: e.target.checked });
        });

        // Confidence slider on dashboard
        const confSlider = document.getElementById('slider-confidence');
        const confValue = document.getElementById('confidence-value');
        if (confSlider) {
            confSlider.addEventListener('input', (e) => {
                confValue.textContent = e.target.value;
            });
            confSlider.addEventListener('change', (e) => {
                this.updateSetting('detection', {
                    confidence: parseFloat(e.target.value),
                    iou_threshold: 0.45
                });
            });
        }

        // Settings page controls
        this.setupSettingsControls();
    }

    setupSettingsControls() {
        // Detection settings
        this.setupRangeSlider('setting-confidence', 'setting-confidence-value', (value) => {
            this.updateSetting('detection', {
                confidence: parseFloat(value),
                iou_threshold: parseFloat(document.getElementById('setting-iou').value)
            });
        });

        this.setupRangeSlider('setting-iou', 'setting-iou-value', (value) => {
            this.updateSetting('detection', {
                confidence: parseFloat(document.getElementById('setting-confidence').value),
                iou_threshold: parseFloat(value)
            });
        });

        // PTZ settings
        document.getElementById('setting-ptz-enabled')?.addEventListener('change', (e) => {
            this.updatePTZSettings();
        });

        this.setupRangeSlider('setting-ptz-speed', 'setting-ptz-speed-value', () => {
            this.updatePTZSettings();
        });

        this.setupRangeSlider('setting-ptz-deadzone', 'setting-ptz-deadzone-value', () => {
            this.updatePTZSettings();
        });

        // Pose settings
        document.getElementById('setting-pose-enabled')?.addEventListener('change', (e) => {
            this.updateSetting('pose', { enabled: e.target.checked });
        });

        // Resolution settings
        document.getElementById('btn-apply-resolution')?.addEventListener('click', () => {
            this.applyResolution();
        });
    }

    async applyResolution() {
        const select = document.getElementById('setting-resolution');
        if (!select) return;

        const [width, height] = select.value.split('x').map(Number);

        try {
            const response = await fetch('/api/settings/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ width, height, quality: 70 })
            });

            const result = await response.json();
            alert(`Resolution set to ${width}x${height}. Please restart CAMAI for changes to take effect.`);
        } catch (e) {
            console.error('Failed to update resolution:', e);
            alert('Failed to update resolution');
        }
    }

    setupRangeSlider(sliderId, valueId, onChange) {
        const slider = document.getElementById(sliderId);
        const valueDisplay = document.getElementById(valueId);

        if (slider && valueDisplay) {
            slider.addEventListener('input', (e) => {
                valueDisplay.textContent = e.target.value;
            });
            slider.addEventListener('change', (e) => {
                onChange(e.target.value);
            });
        }
    }

    updatePTZSettings() {
        this.updateSetting('ptz', {
            enabled: document.getElementById('setting-ptz-enabled')?.checked || false,
            track_speed: parseFloat(document.getElementById('setting-ptz-speed')?.value || 0.5),
            deadzone: parseFloat(document.getElementById('setting-ptz-deadzone')?.value || 0.15)
        });
    }

    async updateSetting(category, settings) {
        try {
            const response = await fetch(`/api/settings/${category}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });

            if (!response.ok) {
                throw new Error('Failed to update settings');
            }

            console.log(`Updated ${category} settings:`, settings);
        } catch (e) {
            console.error('Failed to update settings:', e);
        }
    }

    // Load Settings
    async loadSettings() {
        try {
            const response = await fetch('/api/settings');
            const settings = await response.json();

            // Dashboard toggles
            const ptzToggle = document.getElementById('toggle-ptz');
            if (ptzToggle) ptzToggle.checked = settings.ptz?.enabled || false;

            const poseToggle = document.getElementById('toggle-pose');
            if (poseToggle) poseToggle.checked = settings.pose?.enabled || false;

            const confSlider = document.getElementById('slider-confidence');
            const confValue = document.getElementById('confidence-value');
            if (confSlider) {
                confSlider.value = settings.detection?.confidence || 0.5;
                if (confValue) confValue.textContent = confSlider.value;
            }

            // Settings page
            this.setSliderValue('setting-confidence', 'setting-confidence-value', settings.detection?.confidence || 0.5);
            this.setSliderValue('setting-iou', 'setting-iou-value', settings.detection?.iou_threshold || 0.45);

            const ptzEnabled = document.getElementById('setting-ptz-enabled');
            if (ptzEnabled) ptzEnabled.checked = settings.ptz?.enabled || false;
            this.setSliderValue('setting-ptz-speed', 'setting-ptz-speed-value', settings.ptz?.track_speed || 0.5);
            this.setSliderValue('setting-ptz-deadzone', 'setting-ptz-deadzone-value', settings.ptz?.deadzone || 0.15);

            const poseEnabled = document.getElementById('setting-pose-enabled');
            if (poseEnabled) poseEnabled.checked = settings.pose?.enabled || false;

            // Resolution
            const resSelect = document.getElementById('setting-resolution');
            if (resSelect && settings.stream) {
                const currentRes = `${settings.stream.width}x${settings.stream.height}`;
                resSelect.value = currentRes;
            }

        } catch (e) {
            console.error('Failed to load settings:', e);
        }
    }

    setSliderValue(sliderId, valueId, value) {
        const slider = document.getElementById(sliderId);
        const display = document.getElementById(valueId);
        if (slider) slider.value = value;
        if (display) display.textContent = value;
    }

    // Events
    async loadEvents() {
        try {
            const response = await fetch('/api/events?limit=50');
            const events = await response.json();

            // Recent events on dashboard
            const recentList = document.getElementById('recent-events');
            if (recentList) {
                if (events.length === 0) {
                    recentList.innerHTML = '<div class="event-item placeholder">No events yet</div>';
                } else {
                    recentList.innerHTML = events.slice(0, 10).map(e => this.renderEventItem(e)).join('');
                }
            }

            // Events grid on events page
            const eventsGrid = document.getElementById('events-grid');
            if (eventsGrid) {
                eventsGrid.innerHTML = events.map(e => this.renderEventCard(e)).join('');
            }
        } catch (e) {
            console.error('Failed to load events:', e);
        }
    }

    renderEventItem(event) {
        const iconClass = event.type?.includes('person') ? 'person' :
                         event.type?.includes('vehicle') ? 'vehicle' : 'package';
        const icon = iconClass === 'person' ? '👤' : iconClass === 'vehicle' ? '🚗' : '📦';
        const time = new Date(event.timestamp * 1000).toLocaleTimeString();

        return `
            <div class="event-item">
                <div class="event-icon ${iconClass}">${icon}</div>
                <div class="event-details">
                    <div class="event-type">${event.type || 'Unknown'}</div>
                    <div class="event-time">${time}</div>
                </div>
            </div>
        `;
    }

    renderEventCard(event) {
        const time = new Date(event.timestamp * 1000).toLocaleString();
        return `
            <div class="event-card">
                <div class="event-type">${event.type || 'Unknown'}</div>
                <div class="event-time">${time}</div>
                <div class="event-class">${event.class || ''}</div>
            </div>
        `;
    }

    addEvent(event) {
        const recentList = document.getElementById('recent-events');
        if (recentList) {
            const placeholder = recentList.querySelector('.placeholder');
            if (placeholder) placeholder.remove();

            const html = this.renderEventItem(event);
            recentList.insertAdjacentHTML('afterbegin', html);

            // Keep only 10 items
            while (recentList.children.length > 10) {
                recentList.lastChild.remove();
            }
        }
    }

    // Snapshots
    async loadSnapshots() {
        try {
            const response = await fetch('/api/snapshots');
            const snapshots = await response.json();

            const grid = document.getElementById('snapshots-grid');
            if (grid) {
                if (snapshots.length === 0) {
                    grid.innerHTML = '<p style="color: var(--text-secondary);">No snapshots yet</p>';
                } else {
                    grid.innerHTML = snapshots.map(s => `
                        <div class="snapshot-item" onclick="window.open('${s.path}', '_blank')">
                            <img src="${s.path}" alt="${s.filename}" loading="lazy">
                            <div class="snapshot-info">${s.filename}</div>
                        </div>
                    `).join('');
                }
            }
        } catch (e) {
            console.error('Failed to load snapshots:', e);
        }
    }
}

// Initialize dashboard
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new CAMAIDashboard();
});
