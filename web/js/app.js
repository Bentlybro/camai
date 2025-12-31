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
        this.setupNotificationControls();
        this.loadSettings();
        this.loadEvents();
        this.loadSnapshots();
        this.startStatsPolling();
        this.startDetectionsPolling();
        this.checkPTZStatus();
        this.setupRecordingsControls();
    }

    setupRecordingsControls() {
        const dateFilter = document.getElementById('recording-date-filter');
        const refreshBtn = document.getElementById('btn-refresh-recordings');

        if (dateFilter) {
            dateFilter.addEventListener('change', (e) => {
                this.loadRecordings(e.target.value || null);
            });
        }

        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                const date = dateFilter?.value || null;
                this.loadRecordings(date);
                this.loadRecordingStats();
            });
        }
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
            this.loadNotifications();
        } else if (pageName === 'stats') {
            this.loadStatsPage();
            this.startStatsPagePolling();
        } else if (pageName === 'system') {
            this.loadSystemStats();
            this.startSystemStatsPolling();
        } else if (pageName === 'recordings') {
            this.loadRecordings();
            this.loadRecordingStats();
        }

        // Stop polling when leaving pages
        if (pageName !== 'system' && this.systemStatsInterval) {
            clearInterval(this.systemStatsInterval);
            this.systemStatsInterval = null;
        }
        if (pageName !== 'stats' && this.statsPageInterval) {
            clearInterval(this.statsPageInterval);
            this.statsPageInterval = null;
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

        // Set modal title - use description if available
        const title = document.getElementById('modal-title');
        if (title) title.textContent = event.description || event.type || 'Event Details';

        // Set modal details
        document.getElementById('modal-type').textContent = event.type || '-';
        // Show description with color if available
        const classText = event.description || event.class || '-';
        const colorInfo = event.color ? ` (${event.color})` : '';
        document.getElementById('modal-class').textContent = classText + colorInfo;
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
            modal.classList.remove('active', 'recording-modal');
            // Stop video if playing
            const video = document.getElementById('modal-video');
            if (video) {
                video.pause();
                video.src = '';
            }
            // Also stop any dynamically added videos
            const embeddedVideo = modal.querySelector('.modal-media video');
            if (embeddedVideo) {
                embeddedVideo.pause();
                embeddedVideo.src = '';
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
        // Stats come from /api/stats which nests system stats under 'system'
        const sysStats = stats.system || stats;

        document.getElementById('stat-fps').textContent = sysStats.fps ?? '--';
        document.getElementById('stat-inference').textContent = sysStats.inference_ms ?? '--';
        document.getElementById('stat-frames').textContent = this.formatNumber(sysStats.frame_count) || '--';
        document.getElementById('stat-tracked').textContent = sysStats.tracked_objects ?? '--';
        document.getElementById('stat-uptime').textContent = sysStats.uptime_formatted || this.formatUptime(sysStats.uptime_seconds) || '--';

        document.getElementById('fps-badge').textContent = `${sysStats.fps ?? '--'} FPS`;
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

    // Live Detections
    startDetectionsPolling() {
        this.fetchDetections();
        this.detectionsInterval = setInterval(() => this.fetchDetections(), 500);
    }

    async fetchDetections() {
        try {
            const response = await fetch('/api/stats/detections');
            const data = await response.json();
            // Debug: log first response
            if (!this._detectionsLogged) {
                console.log('Detections API response:', data);
                this._detectionsLogged = true;
            }
            this.renderDetections(data.detections || []);
        } catch (e) {
            console.error('Detections fetch error:', e);
        }
    }

    renderDetections(detections) {
        const container = document.getElementById('live-detections');
        const countBadge = document.getElementById('detection-count');
        if (!container) return;

        // Update count badge
        if (countBadge) {
            countBadge.textContent = detections.length;
        }

        if (detections.length === 0) {
            container.innerHTML = '<div class="detection-empty">No objects detected</div>';
            return;
        }

        // Group by status for better organization
        const grouped = {
            active: detections.filter(d => d.status === 'active'),
            stopped: detections.filter(d => d.status === 'stopped'),
            parked: detections.filter(d => d.status === 'parked')
        };

        let html = '';

        // Active detections first
        for (const det of grouped.active) {
            html += this.renderDetectionItem(det);
        }

        // Stopped vehicles
        for (const det of grouped.stopped) {
            html += this.renderDetectionItem(det);
        }

        // Parked vehicles
        for (const det of grouped.parked) {
            html += this.renderDetectionItem(det);
        }

        container.innerHTML = html;
    }

    renderDetectionItem(det) {
        const iconClass = det.class === 'person' ? 'person' :
                         (det.class === 'car' || det.class === 'truck') ? 'vehicle' : 'package';
        const icon = iconClass === 'person' ? '👤' : iconClass === 'vehicle' ? '🚗' : '📦';

        const statusClass = det.status === 'active' ? '' :
                           det.status === 'stopped' ? 'status-stopped' : 'status-parked';

        const statusLabel = det.status === 'active' ? '' :
                           det.status === 'stopped' ? 'Stopped' : 'Parked';

        const confidence = det.confidence ? `${Math.round(det.confidence * 100)}%` : '';

        return `
            <div class="detection-item ${statusClass}">
                <div class="detection-icon ${iconClass}">${icon}</div>
                <div class="detection-info">
                    <div class="detection-desc">${det.description || det.class}</div>
                    <div class="detection-meta">
                        ${confidence ? `<span class="detection-conf">${confidence}</span>` : ''}
                        ${statusLabel ? `<span class="detection-status">${statusLabel}</span>` : ''}
                    </div>
                </div>
            </div>
        `;
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

        // Camera control buttons (light/night mode)
        document.getElementById('btn-ir-light')?.addEventListener('click', () => {
            this.toggleIRLight();
        });

        document.getElementById('btn-night-mode')?.addEventListener('click', () => {
            this.toggleNightMode();
        });

        document.getElementById('btn-ptz-reset')?.addEventListener('click', () => {
            this.confirmPTZReset();
        });

        // Load presets and imaging status
        this.loadPTZPresets();
        this.loadImagingStatus();
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

    async loadImagingStatus() {
        try {
            const response = await fetch('/api/ptz/imaging');
            const status = await response.json();

            // Update button states
            const lightBtn = document.getElementById('btn-ir-light');
            const nightBtn = document.getElementById('btn-night-mode');

            if (lightBtn) {
                lightBtn.classList.toggle('active', status.ir_light);
            }
            if (nightBtn) {
                nightBtn.classList.toggle('active', status.night_mode);
            }
        } catch (e) {
            console.error('Failed to load imaging status:', e);
        }
    }

    async toggleIRLight() {
        const btn = document.getElementById('btn-ir-light');
        const isActive = btn?.classList.contains('active');

        try {
            const response = await fetch(`/api/ptz/light?enabled=${!isActive}`, {
                method: 'POST'
            });
            const result = await response.json();

            if (btn) {
                btn.classList.toggle('active', result.ir_light);
            }

            if (result.status === 'unsupported') {
                console.warn('IR light control not supported by camera');
            }
        } catch (e) {
            console.error('Failed to toggle IR light:', e);
        }
    }

    async toggleNightMode() {
        const btn = document.getElementById('btn-night-mode');
        const isActive = btn?.classList.contains('active');

        try {
            const response = await fetch(`/api/ptz/night-mode?enabled=${!isActive}`, {
                method: 'POST'
            });
            const result = await response.json();

            if (btn) {
                btn.classList.toggle('active', result.night_mode);
            }

            if (result.status === 'unsupported') {
                console.warn('Night mode control not supported by camera');
            }
        } catch (e) {
            console.error('Failed to toggle night mode:', e);
        }
    }

    confirmPTZReset() {
        if (confirm('Are you sure you want to reset the camera?\n\nThis will perform a pan/tilt correction which recalibrates the camera position.')) {
            this.ptzReset();
        }
    }

    async ptzReset() {
        const btn = document.getElementById('btn-ptz-reset');
        if (btn) {
            btn.disabled = true;
            btn.querySelector('.camera-btn-label').textContent = 'Resetting...';
        }

        try {
            const response = await fetch('/api/ptz/reset', {
                method: 'POST'
            });
            const result = await response.json();

            if (result.status === 'ok') {
                alert('Pan/tilt correction initiated. The camera will recalibrate its position.');
                // Update auto-track toggle since it gets disabled during reset
                const toggle = document.getElementById('toggle-ptz');
                if (toggle) toggle.checked = false;
            } else {
                alert(result.message || 'Reset command not supported by this camera.');
            }
        } catch (e) {
            console.error('Failed to reset PTZ:', e);
            alert('Failed to send reset command to camera.');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.querySelector('.camera-btn-label').textContent = 'Reset';
            }
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

        // Classifier settings
        document.getElementById('setting-classifier-enabled')?.addEventListener('change', (e) => {
            this.updateSetting('classifier', { enabled: e.target.checked });
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

            const classifierEnabled = document.getElementById('setting-classifier-enabled');
            if (classifierEnabled) classifierEnabled.checked = settings.classifier?.enabled !== false; // Default true

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
        // Use description if available (e.g., "black truck"), otherwise fallback to type
        const displayName = event.description || event.type || 'Unknown';

        return `
            <div class="event-item clickable" data-event-index="${index}" onclick="dashboard.openEventByIndex(${index})">
                <div class="event-icon ${iconClass}">${icon}</div>
                <div class="event-details">
                    <div class="event-type">${displayName}</div>
                    <div class="event-time">${time}</div>
                </div>
            </div>
        `;
    }

    renderEventCard(event, index) {
        const time = new Date(event.timestamp * 1000).toLocaleString();
        const confidence = event.confidence ? `${(event.confidence * 100).toFixed(0)}%` : '';
        // Use description if available (e.g., "black truck"), otherwise fallback to class
        const displayClass = event.description || event.class || '';
        // Show color badge if available
        const colorBadge = event.color ? `<span class="color-badge" style="background: ${event.color}">${event.color}</span>` : '';
        return `
            <div class="event-card clickable" data-event-index="${index}" onclick="dashboard.openEventByIndex(${index})">
                <div class="event-type">${event.type || 'Unknown'}</div>
                <div class="event-time">${time}</div>
                <div class="event-class">${displayClass} ${colorBadge}</div>
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

    // Notifications
    setupNotificationControls() {
        // Save button
        document.getElementById('btn-save-notifications')?.addEventListener('click', () => {
            this.saveNotifications();
        });

        // Test buttons
        document.getElementById('btn-test-discord')?.addEventListener('click', () => {
            this.testDiscord();
        });

        document.getElementById('btn-test-mqtt')?.addEventListener('click', () => {
            this.testMQTT();
        });
    }

    async loadNotifications() {
        try {
            const response = await fetch('/api/settings/notifications');
            const settings = await response.json();

            // Discord settings
            const discordEnabled = document.getElementById('setting-discord-enabled');
            if (discordEnabled) discordEnabled.checked = settings.discord?.enabled || false;

            const discordWebhook = document.getElementById('setting-discord-webhook');
            if (discordWebhook) discordWebhook.value = settings.discord?.webhook_url || '';

            // MQTT settings
            const mqttEnabled = document.getElementById('setting-mqtt-enabled');
            if (mqttEnabled) mqttEnabled.checked = settings.mqtt?.enabled || false;

            const mqttBroker = document.getElementById('setting-mqtt-broker');
            if (mqttBroker) mqttBroker.value = settings.mqtt?.broker || 'localhost';

            const mqttPort = document.getElementById('setting-mqtt-port');
            if (mqttPort) mqttPort.value = settings.mqtt?.port || 1883;

            const mqttTopic = document.getElementById('setting-mqtt-topic');
            if (mqttTopic) mqttTopic.value = settings.mqtt?.topic || 'camai/events';

        } catch (e) {
            console.error('Failed to load notification settings:', e);
        }
    }

    async saveNotifications() {
        const settings = {
            discord: {
                enabled: document.getElementById('setting-discord-enabled')?.checked || false,
                webhook_url: document.getElementById('setting-discord-webhook')?.value || ''
            },
            mqtt: {
                enabled: document.getElementById('setting-mqtt-enabled')?.checked || false,
                broker: document.getElementById('setting-mqtt-broker')?.value || 'localhost',
                port: parseInt(document.getElementById('setting-mqtt-port')?.value) || 1883,
                topic: document.getElementById('setting-mqtt-topic')?.value || 'camai/events'
            },
            save_snapshots: true
        };

        try {
            const response = await fetch('/api/settings/notifications', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });

            const result = await response.json();
            if (result.status === 'ok') {
                alert('Notification settings saved!');
            } else {
                alert('Failed to save notification settings');
            }
        } catch (e) {
            console.error('Failed to save notifications:', e);
            alert('Failed to save notification settings');
        }
    }

    async testDiscord() {
        const webhook = document.getElementById('setting-discord-webhook')?.value;
        if (!webhook) {
            alert('Please enter a Discord webhook URL first');
            return;
        }

        try {
            const response = await fetch(webhook, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content: 'CAMAI Test Message - Notifications are working!'
                })
            });

            if (response.ok) {
                alert('Test message sent to Discord!');
            } else {
                alert('Failed to send test message. Check your webhook URL.');
            }
        } catch (e) {
            console.error('Discord test failed:', e);
            alert('Failed to send test message. Check your webhook URL.');
        }
    }

    async testMQTT() {
        // MQTT test requires server-side implementation
        alert('MQTT test requires the system to be running. Enable MQTT and save settings, then check your MQTT broker for messages.');
    }

    // Stats Page
    startStatsPagePolling() {
        if (this.statsPageInterval) {
            clearInterval(this.statsPageInterval);
        }
        // Refresh stats page every 5 seconds
        this.statsPageInterval = setInterval(() => this.loadStatsPage(), 5000);
    }

    async loadStatsPage() {
        try {
            const response = await fetch('/api/stats');
            const stats = await response.json();

            // Summary cards
            document.getElementById('stats-total-people').textContent = stats.summary?.person_events || 0;
            document.getElementById('stats-total-vehicles').textContent = stats.summary?.vehicle_events || 0;
            document.getElementById('stats-total-packages').textContent = stats.summary?.package_events || 0;
            document.getElementById('stats-total-events').textContent = stats.summary?.total_events_today || 0;

            // System stats
            document.getElementById('stats-avg-fps').textContent = `${stats.system?.fps || '--'} FPS`;
            document.getElementById('stats-avg-inference').textContent = `${stats.system?.inference_ms || '--'} ms`;
            document.getElementById('stats-total-frames').textContent = this.formatNumber(stats.system?.frame_count) || '--';
            document.getElementById('stats-uptime').textContent = stats.system?.uptime_formatted || '--';

            // Detection breakdown percentages
            const total = (stats.detection_breakdown?.person || 0) +
                         (stats.detection_breakdown?.vehicle || 0) +
                         (stats.detection_breakdown?.package || 0);

            if (total > 0) {
                const personPct = Math.round((stats.detection_breakdown?.person || 0) / total * 100);
                const vehiclePct = Math.round((stats.detection_breakdown?.vehicle || 0) / total * 100);
                const packagePct = Math.round((stats.detection_breakdown?.package || 0) / total * 100);

                document.getElementById('breakdown-person').style.width = `${personPct}%`;
                document.getElementById('breakdown-person-pct').textContent = `${personPct}%`;

                document.getElementById('breakdown-vehicle').style.width = `${vehiclePct}%`;
                document.getElementById('breakdown-vehicle-pct').textContent = `${vehiclePct}%`;

                document.getElementById('breakdown-package').style.width = `${packagePct}%`;
                document.getElementById('breakdown-package-pct').textContent = `${packagePct}%`;
            }

            // Hourly bars
            this.renderHourlyBars(stats.hourly || []);

        } catch (e) {
            console.error('Failed to load stats:', e);
        }
    }

    renderHourlyBars(hourlyData) {
        const container = document.getElementById('hourly-bars');
        if (!container) return;

        // Find max for scaling
        const maxCount = Math.max(...hourlyData.map(h => h.count), 1);
        const maxHeight = 100; // max bar height in pixels

        // Only show hours with activity or current hour
        const currentHour = new Date().getHours();

        container.innerHTML = hourlyData.map(h => {
            // Calculate height in pixels (minimum 4px if there are events, 2px otherwise)
            const height = h.count > 0
                ? Math.max(8, Math.round((h.count / maxCount) * maxHeight))
                : 2;
            const isCurrentHour = h.hour === currentHour;
            return `
                <div class="hourly-bar-item ${isCurrentHour ? 'current' : ''}">
                    <div class="hourly-bar" style="height: ${height}px" title="${h.count} events">
                        ${h.count > 0 ? `<span class="bar-count">${h.count}</span>` : ''}
                    </div>
                    <span class="hourly-label">${h.hour}</span>
                </div>
            `;
        }).join('');
    }

    // System Stats
    startSystemStatsPolling() {
        if (this.systemStatsInterval) {
            clearInterval(this.systemStatsInterval);
        }
        // Poll every 2 seconds
        this.systemStatsInterval = setInterval(() => this.loadSystemStats(), 2000);
    }

    async loadSystemStats() {
        try {
            const response = await fetch('/api/system');
            const data = await response.json();
            this.updateSystemStats(data);
        } catch (e) {
            console.error('Failed to load system stats:', e);
        }
    }

    updateSystemStats(data) {
        // CPU
        if (data.cpu) {
            const cpuUsage = data.cpu.usage_percent || 0;
            document.getElementById('cpu-badge').textContent = `${cpuUsage}%`;
            document.getElementById('cpu-bar').style.width = `${cpuUsage}%`;
            document.getElementById('cpu-cores').textContent = data.cpu.cores || '--';
            if (data.cpu.load_avg && data.cpu.load_avg.some(l => l > 0)) {
                document.getElementById('cpu-load').textContent = data.cpu.load_avg.map(l => l.toFixed(2)).join(', ');
            } else {
                document.getElementById('cpu-load').textContent = '--';
            }
        }

        // Memory
        if (data.memory) {
            const memUsage = data.memory.usage_percent || 0;
            document.getElementById('mem-badge').textContent = `${memUsage}%`;
            document.getElementById('mem-bar').style.width = `${memUsage}%`;
            document.getElementById('mem-used').textContent = `${data.memory.used_gb || 0} GB`;
            document.getElementById('mem-total').textContent = `${data.memory.total_gb || 0} GB`;
        }

        // GPU
        if (data.gpu) {
            const gpuUsage = data.gpu.usage_percent || 0;
            document.getElementById('gpu-badge').textContent = data.gpu.available ? `${gpuUsage}%` : 'N/A';
            document.getElementById('gpu-bar').style.width = `${gpuUsage}%`;
            document.getElementById('gpu-name').textContent = data.gpu.name || '--';
            if (data.gpu.memory_total_mb > 0) {
                document.getElementById('gpu-mem').textContent = data.gpu.note ||
                    `${Math.round(data.gpu.memory_used_mb)} / ${Math.round(data.gpu.memory_total_mb)} MB`;
            } else {
                document.getElementById('gpu-mem').textContent = data.gpu.note || '--';
            }
        }

        // Disk
        if (data.disk) {
            const diskUsage = data.disk.usage_percent || 0;
            document.getElementById('disk-badge').textContent = `${diskUsage}%`;
            document.getElementById('disk-bar').style.width = `${diskUsage}%`;
            document.getElementById('disk-used').textContent = `${data.disk.used_gb || 0} GB`;
            document.getElementById('disk-free').textContent = `${data.disk.free_gb || 0} GB`;
        }

        // Temperature
        if (data.temperature) {
            this.updateTemperatureDisplay(data.temperature);
        }

        // Network
        if (data.network) {
            this.updateNetworkDisplay(data.network);
        }

        // System Info
        if (data.system) {
            document.getElementById('system-hostname').textContent = data.system.hostname || '--';
            document.getElementById('sysinfo-hostname').textContent = data.system.hostname || '--';
            document.getElementById('sysinfo-uptime').textContent = data.system.uptime_formatted || '--';
            document.getElementById('sysinfo-kernel').textContent = data.system.kernel || '--';
            document.getElementById('sysinfo-model').textContent = data.system.model || (data.is_jetson ? 'Jetson Device' : 'Linux System');
        }
    }

    updateTemperatureDisplay(temps) {
        const container = document.getElementById('temp-grid');
        const badge = document.getElementById('temp-badge');
        if (!container) return;

        // Get max temp for badge
        const maxTemp = temps._max || 0;
        badge.textContent = `${maxTemp}°C`;
        badge.classList.toggle('hot', maxTemp > 70);

        // Only show main temperature sensors (CPU, GPU, and overall)
        const importantSensors = ['cpu', 'gpu', 'CPU', 'GPU', 'CPU-therm', 'GPU-therm', 'tj', 'Tboard'];
        const tempItems = Object.entries(temps)
            .filter(([key]) => {
                // Skip internal keys
                if (key.startsWith('_')) return false;
                // Only include important sensors
                return importantSensors.some(s => key.toLowerCase().includes(s.toLowerCase()));
            })
            // Deduplicate by normalizing names (keep first of each type)
            .reduce((acc, [name, value]) => {
                const normalized = name.toLowerCase().includes('cpu') ? 'CPU' :
                                   name.toLowerCase().includes('gpu') ? 'GPU' : name;
                if (!acc.find(([n]) => n === normalized)) {
                    acc.push([normalized, value]);
                }
                return acc;
            }, [])
            .map(([name, value]) => {
                const tempClass = value > 70 ? 'hot' : value > 50 ? 'warm' : '';
                return `
                    <div class="temp-item">
                        <span class="temp-value ${tempClass}">${value}°C</span>
                        <span class="temp-label">${name}</span>
                    </div>
                `;
            });

        if (tempItems.length === 0) {
            container.innerHTML = '<div class="temp-item"><span class="temp-label">No sensors found</span></div>';
        } else {
            container.innerHTML = tempItems.join('');
        }
    }

    updateNetworkDisplay(network) {
        const container = document.getElementById('network-grid');
        if (!container) return;

        // Only show real network interfaces (ethernet and wifi)
        // Skip virtual interfaces: lo, docker, veth, br-, virbr, can, usb, l4t
        const virtualPrefixes = ['lo', 'docker', 'veth', 'br-', 'virbr', 'can', 'usb', 'l4t', 'dummy'];

        const items = Object.entries(network)
            .filter(([iface]) => {
                const lower = iface.toLowerCase();
                return !virtualPrefixes.some(prefix => lower.startsWith(prefix));
            })
            .filter(([, stats]) => {
                // Also filter out interfaces with zero traffic
                return stats.rx_bytes > 0 || stats.tx_bytes > 0;
            })
            .map(([iface, stats]) => {
                // Friendly name for interface
                let friendlyName = iface;
                if (iface.startsWith('wl') || iface.includes('wlan')) {
                    friendlyName = `WiFi (${iface})`;
                } else if (iface.startsWith('en') || iface.startsWith('eth')) {
                    friendlyName = `Ethernet (${iface})`;
                }

                return `
                    <div class="network-item">
                        <span class="network-iface">${friendlyName}</span>
                        <div class="network-stats">
                            <span class="network-rx">↓ ${this.formatBytes(stats.rx_bytes)}</span>
                            <span class="network-tx">↑ ${this.formatBytes(stats.tx_bytes)}</span>
                        </div>
                    </div>
                `;
            });

        if (items.length === 0) {
            container.innerHTML = '<div class="network-item"><span class="network-label">No active interfaces</span></div>';
        } else {
            container.innerHTML = items.join('');
        }
    }

    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    // ==================== RECORDINGS ====================

    async loadRecordings(date = null) {
        const grid = document.getElementById('recordings-grid');
        if (!grid) return;

        try {
            let url = '/api/recordings?limit=50';
            if (date) url += `&date=${date}`;

            const res = await fetch(url);
            const data = await res.json();

            if (!data.recordings || data.recordings.length === 0) {
                grid.innerHTML = '<div class="recording-empty">No recordings found</div>';
                return;
            }

            grid.innerHTML = data.recordings.map(rec => `
                <div class="recording-card" data-id="${rec.id}" data-path="${rec.path}">
                    <div class="recording-thumbnail">
                        ${rec.thumbnail_path
                            ? `<img src="/api/recordings/${rec.id}/thumbnail" alt="Thumbnail">`
                            : '<span class="no-thumb">🎬</span>'
                        }
                        <span class="recording-duration-badge">${rec.formatted_duration || '--:--'}</span>
                    </div>
                    <div class="recording-info">
                        <div class="recording-title">${rec.filename || 'Recording'}</div>
                        <div class="recording-meta">
                            <span>${rec.formatted_time || this.formatTimestamp(rec.start_time)}</span>
                            <span>${rec.formatted_size || '--'}</span>
                        </div>
                    </div>
                </div>
            `).join('');

            // Add click handlers
            grid.querySelectorAll('.recording-card').forEach(card => {
                card.addEventListener('click', () => {
                    const id = card.dataset.id;
                    this.openRecordingModal(id, data.recordings.find(r => r.id == id));
                });
            });

        } catch (err) {
            console.error('Failed to load recordings:', err);
            grid.innerHTML = '<div class="recording-empty">Failed to load recordings</div>';
        }
    }

    async loadRecordingStats() {
        try {
            const res = await fetch('/api/recordings/stats');
            const stats = await res.json();

            const countEl = document.getElementById('rec-total-count');
            const durationEl = document.getElementById('rec-total-duration');
            const sizeEl = document.getElementById('rec-total-size');
            const retentionEl = document.getElementById('rec-retention');

            if (countEl) countEl.textContent = stats.total_recordings || 0;
            if (sizeEl) sizeEl.textContent = stats.formatted_size || '0 GB';
            if (durationEl) durationEl.textContent = stats.formatted_duration || '0h 0m';
            if (retentionEl && stats.storage?.retention_days) {
                retentionEl.textContent = stats.storage.retention_days;
            }

        } catch (err) {
            console.error('Failed to load recording stats:', err);
        }
    }

    openRecordingModal(id, recording) {
        const modal = document.getElementById('event-modal');
        if (!modal) return;

        const title = document.getElementById('modal-title');
        const mediaContainer = modal.querySelector('.modal-media');
        const detailsContainer = modal.querySelector('.modal-details');

        if (title) title.textContent = recording?.filename || 'Recording';

        // Show video player
        if (mediaContainer) {
            mediaContainer.innerHTML = `
                <video controls autoplay>
                    <source src="/api/recordings/${id}/stream" type="video/mp4">
                    Your browser does not support video playback.
                </video>
            `;
        }

        // Show details
        if (detailsContainer) {
            detailsContainer.innerHTML = `
                <div class="detail-row">
                    <span class="detail-label">Date</span>
                    <span class="detail-value">${recording?.formatted_time || '--'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Duration</span>
                    <span class="detail-value">${recording?.formatted_duration || '--'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Size</span>
                    <span class="detail-value">${recording?.formatted_size || '--'}</span>
                </div>
                <div class="recording-actions">
                    <button class="btn-danger" onclick="dashboard.deleteRecording(${id})">Delete Recording</button>
                </div>
            `;
        }

        modal.classList.add('active', 'recording-modal');
    }

    async deleteRecording(id) {
        if (!confirm('Are you sure you want to delete this recording?')) return;

        try {
            const res = await fetch(`/api/recordings/${id}`, { method: 'DELETE' });
            if (res.ok) {
                this.closeModal();
                this.loadRecordings();
                this.loadRecordingStats();
            } else {
                alert('Failed to delete recording');
            }
        } catch (err) {
            console.error('Failed to delete recording:', err);
            alert('Failed to delete recording');
        }
    }

    formatTimestamp(ts) {
        if (!ts) return '--';
        const date = new Date(ts * 1000);
        return date.toLocaleString();
    }
}

// Initialize dashboard
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new CAMAIDashboard();
});
