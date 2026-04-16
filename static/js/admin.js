const adminState = window.VOCFLIGHT_ADMIN_BOOTSTRAP || {};

const settingsForm = document.getElementById('settings-form');
const settingsStatus = document.getElementById('settings-status');
const registrationStatusPill = document.getElementById('registration-status-pill');
const refreshLogsBtn = document.getElementById('refresh-logs-btn');
const usersTableBody = document.getElementById('users-table-body');
const adminLogList = document.getElementById('admin-log-list');
const chatLogList = document.getElementById('chat-log-list');

settingsForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    settingsStatus.textContent = 'Saving...';

    const enabledModels = Array.from(document.querySelectorAll('.model-checkbox:checked')).map(input => input.value);
    const payload = {
        registration_enabled: document.getElementById('registration-enabled').checked,
        registration_password: document.getElementById('registration-password').value,
        clear_registration_password: document.getElementById('clear-registration-password').checked,
        enabled_models: enabledModels,
    };

    try {
        const response = await fetch('/api/admin/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);

        settingsStatus.textContent = 'Saved.';
        document.getElementById('registration-password').value = '';
        document.getElementById('clear-registration-password').checked = false;
        updateRegistrationPill(Boolean(data.config?.registration_enabled));
    } catch (error) {
        settingsStatus.textContent = error.message || 'Failed to save settings.';
    }
});

usersTableBody?.addEventListener('click', async (event) => {
    const button = event.target.closest('.delete-user-btn');
    if (!button) return;

    const userId = button.dataset.userId;
    const username = button.dataset.username || 'this user';
    if (!window.confirm(`Delete ${username}? This cannot be undone.`)) return;

    button.disabled = true;
    try {
        const response = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        button.closest('tr')?.remove();
    } catch (error) {
        button.disabled = false;
        window.alert(error.message || 'Failed to delete user.');
    }
});

refreshLogsBtn?.addEventListener('click', async () => {
    refreshLogsBtn.disabled = true;
    refreshLogsBtn.textContent = 'Refreshing...';
    try {
        const response = await fetch('/api/admin/logs');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        renderAdminLogs(data.admin_logs || []);
        renderChatLogs(data.chat_logs || []);
    } catch (error) {
        window.alert(error.message || 'Failed to refresh logs.');
    } finally {
        refreshLogsBtn.disabled = false;
        refreshLogsBtn.textContent = 'Refresh';
    }
});

function updateRegistrationPill(isEnabled) {
    if (!registrationStatusPill) return;
    registrationStatusPill.textContent = isEnabled ? 'Registration On' : 'Registration Off';
    registrationStatusPill.className = `status-pill ${isEnabled ? 'admin-on' : 'muted'}`;
}

function formatDetails(details) {
    if (!details || typeof details !== 'object') return '';
    return Object.entries(details).map(([key, value]) => {
        const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        return `<div class="admin-log-detail-row"><span class="detail-label">${escapeHTML(label)}:</span> ${escapeHTML(String(value))}</div>`;
    }).join('');
}

function truncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.slice(0, max) + '...' : str;
}

function formatChatLogDetails(log) {
    let html = '';
    const req = log.request_payload || {};
    const res = log.response_payload || {};

    // New compact format
    if (req.prompt) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Prompt:</span> ${escapeHTML(req.prompt)}</div>`;
    } else if (req.messages && Array.isArray(req.messages)) {
        // Legacy format fallback — show last 2 messages
        req.messages.slice(-2).forEach(msg => {
            html += `<div class="admin-log-detail-row"><span class="detail-label">${escapeHTML((msg.role || '').charAt(0).toUpperCase() + (msg.role || '').slice(1))}:</span> ${escapeHTML(truncate(msg.content, 200))}</div>`;
        });
    }
    if (req.model) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Model:</span> ${escapeHTML(req.model)}</div>`;
    }
    if (req.message_count) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Conversation:</span> ${req.message_count} messages</div>`;
    }
    if (res.message) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Response:</span> ${escapeHTML(truncate(res.message, 300))}</div>`;
    }
    if (res.search && typeof res.search === 'object') {
        const sp = res.search;
        const dates = (sp.dates || []).join(', ');
        html += `<div class="admin-log-detail-row"><span class="detail-label">Search:</span> ${escapeHTML(sp.origin || '')} &rarr; ${escapeHTML(sp.destination || '')} (${escapeHTML(sp.cabin || 'business')}) ${escapeHTML(dates)}</div>`;
    } else if (res.search_params && typeof res.search_params === 'object') {
        const sp = res.search_params;
        html += `<div class="admin-log-detail-row"><span class="detail-label">Search:</span> ${escapeHTML(sp.origin || '')} &rarr; ${escapeHTML(sp.destination || '')} (${escapeHTML(sp.cabin || 'business')})</div>`;
    }
    if (res.outbound_count !== undefined) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Results:</span> ${res.outbound_count} outbound, ${res.return_count || 0} return, ${res.rt_promo_count || 0} RT promo</div>`;
    } else if (res.flights && Array.isArray(res.flights)) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Results:</span> ${res.flights.length} flights found</div>`;
    }
    if (res.best_deal) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Best Deal:</span> ${escapeHTML(String(res.best_deal))}</div>`;
    }
    if (res.search_time_s) {
        let timing = `${res.search_time_s}s`;
        if (res.total_time_s) timing += ` (total: ${res.total_time_s}s)`;
        html += `<div class="admin-log-detail-row"><span class="detail-label">Timing:</span> ${timing}</div>`;
    }
    if (res.error) {
        html += `<div class="admin-log-detail-row"><span class="detail-label">Error:</span> ${escapeHTML(res.error)}</div>`;
    }
    return html;
}

function renderAdminLogs(logs) {
    if (!adminLogList) return;
    if (!logs.length) {
        adminLogList.innerHTML = '<div class="admin-empty">No admin logs yet.</div>';
        return;
    }
    adminLogList.innerHTML = logs.map(log => `
        <div class="admin-log-item">
            <div class="admin-log-top">
                <strong>${escapeHTML(log.action || '')}</strong>
                <span>${escapeHTML(log.admin_username || '')}</span>
                <span>${escapeHTML(log.created_at || '')}</span>
            </div>
            <div class="admin-log-details">${formatDetails(log.details)}</div>
        </div>
    `).join('');
}

function renderChatLogs(logs) {
    if (!chatLogList) return;
    if (!logs.length) {
        chatLogList.innerHTML = '<div class="admin-empty">No chat logs yet.</div>';
        return;
    }
    chatLogList.innerHTML = logs.map(log => `
        <div class="admin-log-item">
            <div class="admin-log-top">
                <strong>${escapeHTML(log.username || '')}</strong>
                <span>${escapeHTML((log.user_role || '').toUpperCase())}</span>
                <span>${escapeHTML(log.session_id || 'local-session')}</span>
                <span>${escapeHTML(log.created_at || '')}</span>
            </div>
            <div class="admin-log-details">${formatChatLogDetails(log)}</div>
        </div>
    `).join('');
}

function escapeHTML(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

updateRegistrationPill(Boolean(adminState.config?.registration_enabled));
