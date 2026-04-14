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
            <pre>${escapeHTML(JSON.stringify(log.details || {}, null, 2))}</pre>
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
            <pre>${escapeHTML(JSON.stringify(log.request_payload || {}, null, 2))}</pre>
            <pre>${escapeHTML(JSON.stringify(log.response_payload || {}, null, 2))}</pre>
        </div>
    `).join('');
}

function escapeHTML(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

updateRegistrationPill(Boolean(adminState.config?.registration_enabled));
