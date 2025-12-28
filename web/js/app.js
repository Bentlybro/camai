// CAMAI Dashboard JavaScript

class CAMAIDashboard {
    constructor() {
        this.ws = null;
        this.reconnectInterval = 5000;
        this.statsInterval = null;
        this.events = [];  // Store events for modal access
        this.currentFilter = 'all';

        this.init();
    }

    init() {
        this.setupNavigation();
        this.setupWebSocket();
        this.setupControls();
        this.setupPTZControls();
        this.setupModal();
        this.setupEventFilter();
        this.setupFullscreen();
        this.loadSettings();
        this.loadEvents();
        this.loadSnapshots();
        this.startStatsPolling();
        this.checkPTZStatus();
    }

    // Fullscreen
    setupFullscreen() {
        const btn = document.getElementById('btn-fullscreen');
        const streamCard = document.getElementById('stream-card');

        if (btn && streamCard) {
            btn.addEventListener('click', () => this.toggleFullscreen(streamCard));

            // Update button icon when fullscreen changes
            document.addEventListener('fullscreenchange', () => {
                btn.innerHTML = document.fullscreenElement ? '&#x2716;' : '&#x26F6;';
            });

            // Also allow double-click on stream to toggle fullscreen
            streamCard.querySelector('.stream-container')?.addEventListener('dblclick', () => {
                this.toggleFullscreen(streamCard);
            });

            // ESC key exits fullscreen (browser handles this, but we update icon)
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && document.fullscreenElement) {
                    // Browser will exit fullscreen, icon updates via fullscreenchange event
                }
            });
        }
    }

    toggleFullscreen(element) {
        if (!document.fullscreenElement) {
            element.requestFullscreen().catch(err => {
                console.error('Fullscreen error:', err);
            });
        } else {
            document.exitFullscreen();
        }
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

    // Modal
    setupModal() {
        const modal = document.getElementById('event-modal');
        if (!modal) return;

        // Close on backdrop click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                this.closeModal();
            }
        });

        // Close on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeModal();
            }
        });
    }

    openModal(event) {
        const modal = document.getElementById('event-modal');
        if (!modal) return;

        // Set modal title
        const title = document.getElementById('modal-title');
        if (title) title.textContent = event.type || 'Event Details';

        // Set modal details
        document.getElementById('modal-type').textContent = event.type || '-';
        document.getElementById('modal-class').textContent = event.class || '-';
        document.getElementById('modal-confidence').textContent =
            event.confidence ? `${(event.confidence * 100).toFixed(1)}%` : '-';
        document.getElementById('modal-time').textContent =
            event.timestamp ? new Date(event.timestamp * 1000).toLocaleString() : '-';

        // Handle media
        const snapshot = document.getElementById('modal-snapshot');
        const video = document.getElementById('modal-video');
        const noMedia = document.getElementById('modal-no-media');

        // Hide all media first
        if (snapshot) snapshot.style.display = 'none';
        if (video) video.style.display = 'none';
        if (noMedia) noMedia.style.display = 'none';

        // Check for video clip
        if (event.video_path) {
            if (video) {
                video.src = event.video_path;
                video.style.display = 'block';
            }
        } else if (event.snapshot_path) {
            if (snapshot) {
                snapshot.src = event.snapshot_path;
                snapshot.style.display = 'block';
            }
        } else {
            if (noMedia) noMedia.style.display = 'block';
        }

        modal.classList.add('active');
    }

    closeModal() {
        const modal = document.getElementById('event-modal');
        if (modal) {
            modal.classList.remove('active');
            // Stop video if playing
            const video = document.getElementById('modal-video');
            if (video) {
                video.pause();
                video.src = '';
            }
        }
    }

    // Event Filter
    setupEventFilter() {
        const filter = document.getElementById('event-filter');
        if (filter) {
            filter.addEventListener('change', (e) => {
                this.currentFilter = e.target.value;
                this.renderFilteredEvents();
            });
        }
    }

    renderFilteredEvents() {
        const eventsGrid = document.getElementById('events-grid');
        if (!eventsGrid) return;

        let filtered = this.events;
        if (this.currentFilter !== 'all') {
            filtered = this.events.filter(e => {
                const type = (e.type || '').toLowerCase();
                const cls = (e.class || '').toLowerCase();
                if (this.currentFilter === 'person') {
                    return type.includes('person') || cls === 'person';
                } else if (this.currentFilter === 'vehicle') {
                    return type.includes('vehicle') || cls === 'car' || cls === 'truck';
                } else if (this.currentFilter === 'package') {
                    return type.includes('package') || cls === 'package';
                }
                return true;
            });
        }

        if (filtered.length === 0) {
            eventsGrid.innerHTML = '<p style="color: var(--text-secondary); padding: 2rem;">No events matching filter</p>';
        } else {
            eventsGrid.innerHTML = filtered.map((e, i) => this.renderEventCard(e, i)).join('');
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
        document.getElementById('stat-uptime').textContent = this.formatUptime(stats.uptime) || '--';

        document.getElementById('fps-badge').textContent = `${stats.fps || '--'} FPS`;
    }

    formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num;
    }

    formatUptime(seconds) {
        if (!seconds) return '--';

        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (days > 0) {
            return `${days}d ${hours}h`;
        } else if (hours > 0) {
            return `${hours}h ${mins}m`;
        } else if (mins > 0) {
            return `${mins}m ${secs}s`;
        } else {
            return `${secs}s`;
        }
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

        // Home button
        document.getElementById('ptz-home')?.addEventListener('click', () => {
            this.ptzHome();
        });

        // Save preset button
        document.getElementById('ptz-save-preset')?.addEventListener('click', () => {
            this.ptzSavePreset();
        });

        // Load presets
        this.loadPTZPresets();
    }

    async ptzHome() {
        try {
            await fetch('/api/ptz/home', { method: 'POST' });
        } catch (e) {
            console.error('PTZ home failed:', e);
        }
    }

    async loadPTZPresets() {
        try {
            const response = await fetch('/api/ptz/presets');
            const presets = await response.json();
            this.renderPTZPresets(presets);
        } catch (e) {
            console.error('Failed to load presets:', e);
        }
    }

    renderPTZPresets(presets) {
        const list = document.getElementById('ptz-presets-list');
        if (!list) return;

        if (!presets || presets.length === 0) {
            list.innerHTML = '<span class="no-presets">No presets</span>';
            return;
        }

        list.innerHTML = presets.map(p => `
            <button class="preset-btn" data-token="${p.token}">
                <span onclick="dashboard.ptzGotoPreset('${p.token}')">${p.name || p.token}</span>
                <span class="delete-preset" onclick="event.stopPropagation(); dashboard.ptzDeletePreset('${p.token}')">&times;</span>
            </button>
        `).join('');
    }

    async ptzSavePreset() {
        const name = prompt('Preset name (optional):');
        if (name === null) return; // Cancelled

        try {
            const url = name ? `/api/ptz/presets?name=${encodeURIComponent(name)}` : '/api/ptz/presets';
            const response = await fetch(url, { method: 'POST' });
            const result = await response.json();
            if (result.status === 'ok') {
                this.loadPTZPresets();
            } else {
                alert('Failed to save preset');
            }
        } catch (e) {
            console.error('Failed to save preset:', e);
            alert('Failed to save preset');
        }
    }

    async ptzGotoPreset(token) {
        try {
            await fetch(`/api/ptz/presets/${token}/goto`, { method: 'POST' });
            // Update toggle to show auto-tracking is now off
            const toggle = document.getElementById('toggle-ptz');
            if (toggle) toggle.checked = false;
        } catch (e) {
            console.error('Failed to goto preset:', e);
        }
    }

    async ptzDeletePreset(token) {
        if (!confirm('Delete this preset?')) return;

        try {
            const response = await fetch(`/api/ptz/presets/${token}`, { method: 'DELETE' });
            const result = await response.json();
            if (result.status === 'ok') {
                this.loadPTZPresets();
            }
        } catch (e) {
            console.error('Failed to delete preset:', e);
        }
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

        // Display/Detection toggles
        document.getElementById('toggle-overlays')?.addEventListener('change', () => this.updateDisplaySettings());
        document.getElementById('toggle-person')?.addEventListener('change', () => this.updateDisplaySettings());
        document.getElementById('toggle-vehicle')?.addEventListener('change', () => this.updateDisplaySettings());
        document.getElementById('toggle-package')?.addEventListener('change', () => this.updateDisplaySettings());

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

        // PTZ connection settings
        document.getElementById('btn-save-ptz-connection')?.addEventListener('click', () => {
            this.savePTZConnection();
        });
    }

    async savePTZConnection() {
        const host = document.getElementById('setting-ptz-host')?.value || '';
        const port = parseInt(document.getElementById('setting-ptz-port')?.value) || 2020;
        const username = document.getElementById('setting-ptz-username')?.value || '';
        const password = document.getElementById('setting-ptz-password')?.value || '';

        try {
            const response = await fetch('/api/settings/ptz/connection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ host, port, username, password })
            });

            const result = await response.json();
            if (result.status === 'ok') {
                alert('PTZ connection saved. Please restart CAMAI to connect.');
                document.getElementById('setting-ptz-password').value = '';
            } else {
                alert('Failed to save PTZ connection');
            }
        } catch (e) {
            console.error('Failed to save PTZ connection:', e);
            alert('Failed to save PTZ connection');
        }
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
            if (result.status === 'ok') {
                alert(`Resolution changed to ${width}x${height}. Stream will restart momentarily.`);
            } else {
                alert('Failed to update resolution');
            }
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

    updateDisplaySettings() {
        this.updateSetting('display', {
            show_overlays: document.getElementById('toggle-overlays')?.checked ?? true,
            detect_person: document.getElementById('toggle-person')?.checked ?? true,
            detect_vehicle: document.getElementById('toggle-vehicle')?.checked ?? true,
            detect_package: document.getElementById('toggle-package')?.checked ?? true
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

            // Display toggles
            const overlaysToggle = document.getElementById('toggle-overlays');
            if (overlaysToggle) overlaysToggle.checked = settings.display?.show_overlays ?? true;

            const personToggle = document.getElementById('toggle-person');
            if (personToggle) personToggle.checked = settings.display?.detect_person ?? true;

            const vehicleToggle = document.getElementById('toggle-vehicle');
            if (vehicleToggle) vehicleToggle.checked = settings.display?.detect_vehicle ?? true;

            const packageToggle = document.getElementById('toggle-package');
            if (packageToggle) packageToggle.checked = settings.display?.detect_package ?? true;

            const confSlider = document.getElementById('slider-confidence');
            const confValue = document.getElementById('confidence-value');
            if (confSlider) {
                confSlider.value = settings.detection?.confidence || 0.5;
                if (confValue) confValue.textContent = confSlider.value;
            }

            // Settings page
            this.setSliderValue('setting-confidence', 'setting-confidence-value', settings.detection?.confidence || 0.5);
            this.setSliderValue('setting-iou', 'setting-iou-value', settings.detection?.iou_threshold || 0.45);

            // PTZ connection
            const ptzHost = document.getElementById('setting-ptz-host');
            if (ptzHost) ptzHost.value = settings.ptz?.host || '';
            const ptzPort = document.getElementById('setting-ptz-port');
            if (ptzPort) ptzPort.value = settings.ptz?.port || 2020;
            const ptzUsername = document.getElementById('setting-ptz-username');
            if (ptzUsername) ptzUsername.value = settings.ptz?.username || '';

            // PTZ tracking
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
            this.events = await response.json();

            // Recent events on dashboard
            const recentList = document.getElementById('recent-events');
            if (recentList) {
                if (this.events.length === 0) {
                    recentList.innerHTML = '<div class="event-item placeholder">No events yet</div>';
                } else {
                    recentList.innerHTML = this.events.slice(0, 10).map((e, i) => this.renderEventItem(e, i)).join('');
                }
            }

            // Events grid on events page - use filtered render
            this.renderFilteredEvents();
        } catch (e) {
            console.error('Failed to load events:', e);
        }
    }

    renderEventItem(event, index) {
        const iconClass = event.type?.includes('person') ? 'person' :
                         event.type?.includes('vehicle') ? 'vehicle' : 'package';
        const icon = iconClass === 'person' ? '👤' : iconClass === 'vehicle' ? '🚗' : '📦';
        const time = new Date(event.timestamp * 1000).toLocaleTimeString();

        return `
            <div class="event-item clickable" data-event-index="${index}" onclick="dashboard.openEventByIndex(${index})">
                <div class="event-icon ${iconClass}">${icon}</div>
                <div class="event-details">
                    <div class="event-type">${event.type || 'Unknown'}</div>
                    <div class="event-time">${time}</div>
                </div>
            </div>
        `;
    }

    renderEventCard(event, index) {
        const time = new Date(event.timestamp * 1000).toLocaleString();
        const confidence = event.confidence ? `${(event.confidence * 100).toFixed(0)}%` : '';
        return `
            <div class="event-card clickable" data-event-index="${index}" onclick="dashboard.openEventByIndex(${index})">
                <div class="event-type">${event.type || 'Unknown'}</div>
                <div class="event-time">${time}</div>
                <div class="event-class">${event.class || ''}</div>
                ${confidence ? `<div class="event-confidence">${confidence} confidence</div>` : ''}
            </div>
        `;
    }

    openEventByIndex(index) {
        if (index >= 0 && index < this.events.length) {
            this.openModal(this.events[index]);
        }
    }

    addEvent(event) {
        // Add to events array
        this.events.unshift(event);
        this.events = this.events.slice(0, 100);  // Keep max 100

        const recentList = document.getElementById('recent-events');
        if (recentList) {
            const placeholder = recentList.querySelector('.placeholder');
            if (placeholder) placeholder.remove();

            // Re-render to update indexes
            recentList.innerHTML = this.events.slice(0, 10).map((e, i) => this.renderEventItem(e, i)).join('');
        }

        // Also update events grid if on events page
        this.renderFilteredEvents();
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
