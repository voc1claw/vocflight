/* ========================================================
   VOCFlight - Chat UI Logic + Local Memory + Sessions
   ======================================================== */

const MODEL_TIERS = {
    'openrouter/elephant-alpha': 'free',
    'z-ai/glm-5.1': 'mid',
    'qwen/qwen3.6-plus': 'mid',
    'minimax/minimax-m2.7': 'mid',
    'anthropic/claude-opus-4.6': 'high',
    'anthropic/claude-sonnet-4.6': 'high',
    'openai/gpt-5.4': 'high',
};

const COMBO_COLORS = [
    { bg: '#EFF6FF', border: '#3B82F6', text: '#1E40AF', label: '#3B82F6' },
    { bg: '#F0FDF4', border: '#22C55E', text: '#166534', label: '#22C55E' },
    { bg: '#FFF7ED', border: '#F97316', text: '#9A3412', label: '#F97316' },
    { bg: '#FDF2F8', border: '#EC4899', text: '#9D174D', label: '#EC4899' },
    { bg: '#F5F3FF', border: '#8B5CF6', text: '#5B21B6', label: '#8B5CF6' },
    { bg: '#ECFDF5', border: '#14B8A6', text: '#115E59', label: '#14B8A6' },
];

const STORAGE_KEYS = {
    PREFS: 'vocflight_preferences',
    SESSIONS: 'vocflight_sessions',
    ACTIVE: 'vocflight_active_session',
    MODEL: 'vocflight_model',
};

const Memory = {
    storageKey(name) {
        return `${STORAGE_KEYS[name]}:${bootstrap.user?.id || 'anon'}`;
    },
    normalizeSession(session) {
        return {
            ...session,
            prefs: session?.prefs || {},
        };
    },
    getSessionPrefs(sessionId = this.getActiveId()) {
        return this.getSession(sessionId)?.prefs || {};
    },
    saveSessionPrefs(sessionId, prefs) {
        if (!sessionId) return;
        this.updateSession(sessionId, { prefs });
        updatePrefsSummary();
    },
    learnFromMessage(sessionId, userText) {
        if (!sessionId) return;
        const t = userText.toLowerCase();
        const p = { ...this.getSessionPrefs(sessionId) };
        let changed = false;

        if (/\bbusiness\s*(class)?\b/.test(t) && !/economy/.test(t)) {
            if (p.cabinPreference !== 'business') { p.cabinPreference = 'business'; changed = true; }
        } else if (/\beconomy\s*(class)?\b/.test(t) && !/business/.test(t)) {
            if (p.cabinPreference !== 'economy') { p.cabinPreference = 'economy'; changed = true; }
        } else if (/\bfirst\s*(class)?\b/.test(t)) {
            if (p.cabinPreference !== 'first') { p.cabinPreference = 'first'; changed = true; }
        }

        if (/\b(round[\s-]?trip|return)\b/.test(t)) {
            if (p.tripTypePreference !== 'round-trip') { p.tripTypePreference = 'round-trip'; changed = true; }
        } else if (/\bone[\s-]?way\b/.test(t)) {
            if (p.tripTypePreference !== 'one-way') { p.tripTypePreference = 'one-way'; changed = true; }
        }

        const fromMatch = t.match(/\b(?:from|departing|leaving)\s+([a-z\s]{2,20})/);
        if (fromMatch) {
            const city = fromMatch[1].trim().replace(/\s+on\b.*/, '').replace(/\s+to\b.*/, '').trim();
            if (city.length >= 2 && city.length <= 20) {
                if (!p.frequentOrigins) p.frequentOrigins = [];
                if (!p.frequentOrigins.includes(city)) {
                    p.frequentOrigins.unshift(city);
                    p.frequentOrigins = p.frequentOrigins.slice(0, 5);
                    changed = true;
                }
            }
        }

        if (/\bnonstop\b|\bdirect\b|\bno stops?\b/.test(t)) {
            if (!p.prefersNonstop) { p.prefersNonstop = true; changed = true; }
        }

        if (/\bcheap(est)?\b|\bbudget\b|\baffordable\b|\blow(est)?\s*price\b/.test(t)) {
            if (p.pricePreference !== 'budget') { p.pricePreference = 'budget'; changed = true; }
        } else if (/\bpremium\b|\bluxury\b|\bbest\s*(quality|comfort)\b/.test(t)) {
            if (p.pricePreference !== 'premium') { p.pricePreference = 'premium'; changed = true; }
        }

        if (changed) {
            p.lastUpdated = new Date().toISOString();
            this.saveSessionPrefs(sessionId, p);
        }
    },
    buildContextForAI(sessionId = this.getActiveId()) {
        const p = this.getSessionPrefs(sessionId);
        const parts = [];
        if (p.cabinPreference) parts.push(`Preferred cabin: ${p.cabinPreference}`);
        if (p.tripTypePreference) parts.push(`Usually books: ${p.tripTypePreference}`);
        if (p.frequentOrigins?.length) parts.push(`Frequent origins: ${p.frequentOrigins.join(', ')}`);
        if (p.prefersNonstop) parts.push('Prefers nonstop/direct flights');
        if (p.pricePreference) parts.push(`Price preference: ${p.pricePreference}`);
        if (p.preferredAirlines?.length) parts.push(`Preferred airlines: ${p.preferredAirlines.join(', ')}`);
        if (p.avoidAirlines?.length) parts.push(`Avoid airlines: ${p.avoidAirlines.join(', ')}`);
        return parts.length ? parts.join('. ') + '.' : '';
    },
    getSessions() {
        try {
            return (JSON.parse(localStorage.getItem(this.storageKey('SESSIONS'))) || []).map(session => this.normalizeSession(session));
        }
        catch { return []; }
    },
    saveSessions(sessions) {
        localStorage.setItem(this.storageKey('SESSIONS'), JSON.stringify(sessions));
    },
    getActiveId() {
        return localStorage.getItem(this.storageKey('ACTIVE')) || null;
    },
    setActiveId(id) {
        localStorage.setItem(this.storageKey('ACTIVE'), id);
    },
    createSession() {
        const sessions = this.getSessions();
        const id = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
        const session = {
            id,
            title: 'New Session',
            messages: [],
            prefs: {},
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
        };
        sessions.unshift(session);
        this.saveSessions(sessions);
        this.setActiveId(id);
        return session;
    },
    getSession(id) {
        const session = this.getSessions().find(s => s.id === id);
        return session ? this.normalizeSession(session) : null;
    },
    updateSession(id, updates) {
        const sessions = this.getSessions();
        const idx = sessions.findIndex(s => s.id === id);
        if (idx >= 0) {
            sessions[idx] = this.normalizeSession({
                ...sessions[idx],
                ...updates,
                updatedAt: new Date().toISOString(),
            });
            this.saveSessions(sessions);
        }
    },
    deleteSession(id) {
        let sessions = this.getSessions();
        sessions = sessions.filter(s => s.id !== id);
        this.saveSessions(sessions);
        if (this.getActiveId() === id) {
            if (sessions.length) this.setActiveId(sessions[0].id);
            else this.setActiveId(null);
        }
    },
    getModel() { return localStorage.getItem(this.storageKey('MODEL')) || 'openai/gpt-5.4'; },
    saveModel(m) { localStorage.setItem(this.storageKey('MODEL'), m); },
    resetAll() {
        localStorage.removeItem(STORAGE_KEYS.PREFS);
        localStorage.removeItem(this.storageKey('SESSIONS'));
        localStorage.removeItem(this.storageKey('ACTIVE'));
        localStorage.removeItem(this.storageKey('MODEL'));
    }
};

let state = { messages: [], isLoading: false, activeSessionId: null };
const bootstrap = normalizeBootstrap(window.VOCFLIGHT_BOOTSTRAP);

const chatContainer = document.getElementById('chat-container');
const messageInput = document.getElementById('message-input');
const sendBtn = document.getElementById('send-btn');
const modelSelect = document.getElementById('model-select');
const tierBadge = document.getElementById('tier-badge');
const sidebar = document.getElementById('sidebar');
const sidebarOverlay = document.getElementById('sidebar-overlay');
const sessionList = document.getElementById('session-list');
const resetDataBtn = document.getElementById('reset-data-btn');
const resetModal = document.getElementById('reset-modal');
const resetCancelBtn = document.getElementById('reset-cancel');
const resetConfirmBtn = document.getElementById('reset-confirm');
const burgerBtn = document.getElementById('burger-btn');
const sidebarCloseBtn = document.getElementById('sidebar-close');
const newSessionBtn = document.getElementById('new-session-btn');

document.addEventListener('DOMContentLoaded', () => {
    localStorage.removeItem(STORAGE_KEYS.PREFS);
    applyBootstrap();
    updateTierBadge();

    let activeId = Memory.getActiveId();
    let session = activeId ? Memory.getSession(activeId) : null;
    if (!session) session = Memory.createSession();

    loadSession(session.id);
    renderSessionList();
    updatePrefsSummary();
    messageInput.focus();
});

sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
messageInput.addEventListener('input', () => {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
});
modelSelect.addEventListener('change', () => {
    updateTierBadge();
    Memory.saveModel(modelSelect.value);
});

burgerBtn?.addEventListener('click', () => {
    sidebar?.classList.add('open');
    sidebarOverlay?.classList.add('open');
});
sidebarCloseBtn?.addEventListener('click', closeSidebar);
sidebarOverlay?.addEventListener('click', closeSidebar);
newSessionBtn?.addEventListener('click', () => {
    const s = Memory.createSession();
    loadSession(s.id);
    renderSessionList();
    closeSidebar();
});

resetDataBtn?.addEventListener('click', () => {
    resetModal?.classList.add('open');
});
resetCancelBtn?.addEventListener('click', () => {
    resetModal?.classList.remove('open');
});
resetConfirmBtn?.addEventListener('click', () => {
    Memory.resetAll();
    const s = Memory.createSession();
    loadSession(s.id);
    renderSessionList();
    updatePrefsSummary();
    applyBootstrap();
    updateTierBadge();
    resetModal?.classList.remove('open');
    closeSidebar();
});

function closeSidebar() {
    sidebar?.classList.remove('open');
    sidebarOverlay?.classList.remove('open');
}

function normalizeBootstrap(data) {
    const fallbackModels = [
        { id: 'openai/gpt-5.4', label: 'GPT 5.4', tier: 'high' },
    ];
    return {
        user: data?.user || null,
        models: Array.isArray(data?.models) && data.models.length ? data.models : fallbackModels,
        is_admin: Boolean(data?.is_admin),
        registration_enabled: data?.registration_enabled !== false,
    };
}

function applyBootstrap() {
    renderUserChrome();
    renderModelOptions(bootstrap.models);
    const allowedIds = new Set(bootstrap.models.map(model => model.id));
    const savedModel = Memory.getModel();
    const nextModel = allowedIds.has(savedModel)
        ? savedModel
        : (allowedIds.has('openai/gpt-5.4') ? 'openai/gpt-5.4' : bootstrap.models[0].id);
    modelSelect.value = nextModel;
    Memory.saveModel(nextModel);
}

function renderUserChrome() {
    if (!bootstrap.user) return;
    const initial = (bootstrap.user.username || 'V').charAt(0).toUpperCase();
    const username = bootstrap.user.username || 'User';
    const role = (bootstrap.user.role || 'member').toUpperCase();
    const updates = [
        ['header-user-avatar', initial],
        ['sidebar-user-avatar', initial],
        ['header-user-name', username],
        ['sidebar-user-name', username],
        ['header-user-role', role],
        ['sidebar-user-meta', role],
    ];
    updates.forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    });
}

function renderModelOptions(models) {
    const tierLabels = { free: 'FREE', mid: 'MID TIER', high: 'HIGH TIER' };
    const tierOrder = ['free', 'mid', 'high'];
    const grouped = models.reduce((acc, model) => {
        const tier = model.tier || 'high';
        if (!acc[tier]) acc[tier] = [];
        acc[tier].push(model);
        return acc;
    }, {});

    modelSelect.innerHTML = '';
    tierOrder.forEach(tier => {
        if (!grouped[tier]?.length) return;
        const optgroup = document.createElement('optgroup');
        optgroup.label = tierLabels[tier] || tier.toUpperCase();
        grouped[tier].forEach(model => {
            const option = document.createElement('option');
            option.value = model.id;
            option.textContent = model.label;
            optgroup.appendChild(option);
        });
        modelSelect.appendChild(optgroup);
    });
}

function loadSession(id) {
    const session = Memory.getSession(id);
    if (!session) return;
    state.activeSessionId = id;
    state.messages = [...session.messages];
    Memory.setActiveId(id);
    chatContainer.innerHTML = '';
    if (state.messages.length === 0) {
        showWelcome();
    } else {
        state.messages.forEach(m => {
            if (m.role === 'user') {
                renderMessage('user', m.content);
            } else if (m.role === 'assistant') {
                renderMessage(
                    'bot',
                    m.content,
                    m.flights || null,
                    m.search_params || null,
                    Boolean(m.isError),
                    m.return_flights || null,
                    m.round_trip_flights || null,
                    m.trip_analysis || null,
                    m.best_deal || null,
                );
            }
        });
    }
    updatePrefsSummary();
    renderSessionList();
    scrollToBottom();
}

function saveCurrentSession() {
    if (!state.activeSessionId) return;
    let title = 'New Session';
    const firstUser = state.messages.find(m => m.role === 'user');
    if (firstUser) title = firstUser.content.slice(0, 40) + (firstUser.content.length > 40 ? '...' : '');
    Memory.updateSession(state.activeSessionId, { messages: state.messages, title });
}

function renderSessionList() {
    if (!sessionList) return;
    const sessions = Memory.getSessions();
    sessionList.innerHTML = '';
    sessions.forEach(s => {
        const el = document.createElement('div');
        el.className = 'session-item' + (s.id === state.activeSessionId ? ' active' : '');
        const date = new Date(s.updatedAt || s.createdAt);
        const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        el.innerHTML = `
            <div class="session-item-content" data-id="${s.id}">
                <div class="session-item-title">${escapeHTML(s.title)}</div>
                <div class="session-item-date">${dateStr} &middot; ${s.messages.length} msgs</div>
            </div>
            <button class="session-delete" data-id="${s.id}" title="Delete session">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>`;
        el.querySelector('.session-item-content').addEventListener('click', () => {
            loadSession(s.id);
            closeSidebar();
        });
        el.querySelector('.session-delete').addEventListener('click', (e) => {
            e.stopPropagation();
            Memory.deleteSession(s.id);
            if (Memory.getSessions().length === 0) {
                const ns = Memory.createSession();
                loadSession(ns.id);
            } else if (s.id === state.activeSessionId) {
                loadSession(Memory.getSessions()[0].id);
            }
            renderSessionList();
        });
        sessionList.appendChild(el);
    });
}

function updatePrefsSummary() {
    const el = document.getElementById('sidebar-prefs-summary');
    if (!el) return;
    const ctx = Memory.buildContextForAI(state.activeSessionId);
    el.textContent = ctx || 'No memory learned in this session yet.';
}

function updateTierBadge() {
    const tier = MODEL_TIERS[modelSelect.value] || 'free';
    tierBadge.textContent = { free: 'FREE', mid: 'MID', high: 'HIGH' }[tier];
    tierBadge.className = 'tier-badge tier-' + tier;
}

function showWelcome() {
    chatContainer.innerHTML = '';
    scrollToBottom();
}

function useSuggestion(btn) {
    messageInput.value = btn.textContent;
    messageInput.focus();
    sendMessage();
}

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || state.isLoading) return;
    const welcome = chatContainer.querySelector('.welcome-container');
    if (welcome) welcome.remove();

    Memory.learnFromMessage(state.activeSessionId, text);
    updatePrefsSummary();

    state.messages.push({ role: 'user', content: text });
    renderMessage('user', text);
    saveCurrentSession();
    messageInput.value = '';
    messageInput.style.height = 'auto';

    state.isLoading = true;
    sendBtn.disabled = true;
    const typingEl = showTypingIndicator();

    try {
        const userContext = Memory.buildContextForAI(state.activeSessionId);
        const response = await callAPI(state.messages, modelSelect.value, userContext);
        typingEl.remove();
        const botText = response.message || 'I couldn\'t generate a response.';
        state.messages.push({
            role: 'assistant',
            content: botText,
            flights: response.flights || null,
            search_params: response.search_params || null,
            return_flights: response.return_flights || null,
            round_trip_flights: response.round_trip_flights || null,
            trip_analysis: response.trip_analysis || null,
            best_deal: response.best_deal || null,
            isError: false,
        });
        saveCurrentSession();
        renderSessionList();
        renderMessage('bot', botText, response.flights, response.search_params, false, response.return_flights, response.round_trip_flights, response.trip_analysis, response.best_deal);
    } catch (error) {
        typingEl.remove();
        const errorText = 'Sorry, something went wrong. Please try again.';
        state.messages.push({
            role: 'assistant',
            content: errorText,
            flights: null,
            search_params: null,
            return_flights: null,
            round_trip_flights: null,
            trip_analysis: null,
            best_deal: null,
            isError: true,
        });
        saveCurrentSession();
        renderSessionList();
        renderMessage('bot', errorText, null, null, true, null, null, null, null);
    }
    state.isLoading = false;
    sendBtn.disabled = false;
    scrollToBottom();
}

async function callAPI(messages, model, userContext = '') {
    const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            messages: messages.map(({ role, content }) => ({ role, content })),
            model,
            user_context: userContext,
            client_session_id: state.activeSessionId,
        }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

function renderMessage(role, text, flights = null, searchParams = null, isError = false, returnFlights = null, roundTripFlights = null, tripAnalysis = null, bestDeal = null) {
    const msg = document.createElement('div');
    msg.className = `message ${role}`;
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    if (role === 'bot') {
        avatar.innerHTML = '&#9992;';
    } else {
        avatar.innerHTML = `
            <svg class="avatar-silhouette" width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="12" cy="8" r="4" fill="currentColor"></circle>
                <path d="M4 20c0-4.2 3.6-7 8-7s8 2.8 8 7" fill="currentColor"></path>
            </svg>
        `;
    }
    const content = document.createElement('div');
    content.className = 'message-content';
    const bubble = document.createElement('div');
    bubble.className = isError ? 'message-bubble error-message' : 'message-bubble';
    bubble.innerHTML = formatMessageText(text);
    content.appendChild(bubble);

    if (searchParams && searchParams.origin && searchParams.destination) {
        const banner = document.createElement('div');
        banner.className = 'search-params';
        const dates = (searchParams.dates || []).map(d => formatDate(d)).join(', ');
        const isRT = returnFlights && returnFlights.length > 0;
        banner.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> ${searchParams.origin} ${isRT ? '&harr;' : '&rarr;'} ${searchParams.destination}${dates ? ' &middot; ' + dates : ''}${searchParams.cabin ? ' &middot; ' + capitalize(searchParams.cabin) : ''}`;
        content.appendChild(banner);
    }
    const hasOutbound = flights && flights.length > 0;
    const hasReturn = returnFlights && returnFlights.length > 0;
    const hasRoundTripPromos = roundTripFlights && roundTripFlights.length > 0;
    if (hasOutbound && hasReturn) content.appendChild(renderRoundTripBox(flights, returnFlights, roundTripFlights, tripAnalysis, searchParams));
    else if (hasRoundTripPromos) content.appendChild(renderPromoRoundTripSection(roundTripFlights, searchParams, tripAnalysis));
    else if (hasOutbound) content.appendChild(renderFlightCards(flights, searchParams));
    if (!hasOutbound && flights && flights.length === 0) {
        const nr = document.createElement('div'); nr.className = 'no-results'; nr.textContent = 'No flights found. Try different dates or routes.'; content.appendChild(nr);
    }
    if (bestDeal) content.appendChild(renderBestDeal(bestDeal));
    msg.appendChild(avatar); msg.appendChild(content); chatContainer.appendChild(msg); scrollToBottom();
}

function renderRoundTripBox(outbound, returnFlights, roundTripFlights, tripAnalysis, searchParams) {
    const wrapper = document.createElement('div'); wrapper.className = 'rt-wrapper';
    const origin = searchParams?.origin || outbound[0]?.departure_airport || '';
    const dest = searchParams?.destination || outbound[0]?.arrival_airport || '';
    const outByDate = groupByDate(outbound); const retByDate = groupByDate(returnFlights);
    const box = document.createElement('div'); box.className = 'rt-box';
    const outSection = document.createElement('div'); outSection.className = 'rt-section';
    outSection.innerHTML = `<div class="rt-section-header rt-out"><span class="rt-dir-icon">&#9992;</span> LEG 1: ${origin} &rarr; ${dest}</div>`;
    Object.keys(outByDate).sort().forEach(d => outSection.appendChild(renderDateGroup(d, outByDate[d], origin, dest)));
    box.appendChild(outSection);
    const divider = document.createElement('div'); divider.className = 'rt-divider'; divider.innerHTML = '<span>ROUND TRIP</span>'; box.appendChild(divider);
    const retSection = document.createElement('div'); retSection.className = 'rt-section';
    retSection.innerHTML = `<div class="rt-section-header rt-ret"><span class="rt-dir-icon">&#9992;</span> LEG 2: ${dest} &rarr; ${origin}</div>`;
    Object.keys(retByDate).sort().forEach(d => retSection.appendChild(renderDateGroup(d, retByDate[d], dest, origin)));
    box.appendChild(retSection); wrapper.appendChild(box);
    if (roundTripFlights?.length) wrapper.appendChild(renderPromoRoundTripSection(roundTripFlights, searchParams, tripAnalysis));
    const combos = buildCombos(outbound, returnFlights); if (combos.length > 0) wrapper.appendChild(renderComboAnalysis(combos, tripAnalysis));
    return wrapper;
}

function renderDateGroup(dateStr, flights, origin, dest) {
    const group = document.createElement('div'); group.className = 'date-group';
    group.innerHTML = `<div class="date-group-label">--- ${origin} &rarr; ${dest}: ${formatDate(dateStr)} ---</div>`;
    flights.forEach((f, i) => group.appendChild(renderFlightRow(f, i + 1)));
    return group;
}

function renderFlightRow(flight, num) {
    const row = document.createElement('div'); row.className = 'flight-row';
    const stopsStr = flight.stops === 0 ? 'Nonstop' : `${flight.stops} stop${flight.stops > 1 ? 's' : ''}`;
    const via = (flight.layovers || []).map(l => l.code).filter(Boolean).join(', ');
    const viaStr = via ? ` (${via})` : '';
    let layoverHTML = ''; (flight.layovers || []).forEach(l => { if (l.duration && l.code) layoverHTML += `<div class="flight-row-layover">Layover: ${escapeHTML(l.duration)} ${escapeHTML(l.code)}</div>`; });
    let fnHTML = ''; if (flight.flight_numbers?.length) fnHTML = `<span class="flight-row-fn">${flight.flight_numbers.join(', ')}</span>`;
    const link = buildGoogleFlightsLink(flight);
    row.innerHTML = `<div class="flight-row-main"><div class="flight-row-num">${num}.</div><div class="flight-row-body"><div class="flight-row-line1"><span class="flight-row-airline">${escapeHTML(flight.airline)}</span><span class="flight-row-stops">${stopsStr}${viaStr}</span><span class="flight-row-dur">${escapeHTML(flight.duration)}</span></div><div class="flight-row-line2"><span>${escapeHTML(flight.departure_time)} &rarr; ${escapeHTML(flight.arrival_time)}</span><span class="flight-row-price">${formatPrice(flight)}</span></div>${layoverHTML}<div class="flight-row-actions">${fnHTML}<a href="${link}" target="_blank" rel="noopener" class="flight-row-link"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg> Google Flights</a></div></div></div>`;
    return row;
}

function renderFlightCards(flights, searchParams) {
    const container = document.createElement('div'); container.className = 'flight-cards-container';
    const origin = searchParams?.origin || flights[0]?.departure_airport || '';
    const dest = searchParams?.destination || flights[0]?.arrival_airport || '';
    const byDate = groupByDate(flights);
    Object.keys(byDate).sort().forEach(d => container.appendChild(renderDateGroup(d, byDate[d], origin, dest)));
    return container;
}

function buildCombos(outbound, returnFlights) {
    const po = outbound.filter(f => f.price != null).sort((a, b) => a.price - b.price);
    const pr = returnFlights.filter(f => f.price != null).sort((a, b) => a.price - b.price);
    if (!po.length || !pr.length) return [];
    const seen = new Set(); const combos = [];
    for (const o of po) {
        for (const r of pr) {
            const key = `${o.ref || o.airline}|${o.departure_date || ''}|${o.departure_time || ''}__${r.ref || r.airline}|${r.departure_date || ''}|${r.departure_time || ''}`;
            if (!seen.has(key)) {
                seen.add(key);
                combos.push({ out: o, ret: r, total: o.price + r.price });
            }
        }
    }
    combos.sort((a, b) => a.total - b.total);
    const labeled = [];
    if (combos.length > 0) labeled.push({ ...combos[0], label: 'CHEAPEST', idx: 0 });
    const fo = [...po].sort((a, b) => parseDur(a.duration) - parseDur(b.duration))[0];
    const fr = [...pr].sort((a, b) => parseDur(a.duration) - parseDur(b.duration))[0];
    if (fo && fr) { const k = `${fo.airline}|${fr.airline}`; if (!labeled.some(l => `${l.out.airline}|${l.ret.airline}` === k)) labeled.push({ out: fo, ret: fr, total: fo.price + fr.price, label: 'FASTEST', idx: labeled.length }); }
    for (const c of combos.slice(1, 6)) { const k = `${c.out.airline}|${c.ret.airline}`; if (!labeled.some(l => `${l.out.airline}|${l.ret.airline}` === k)) { labeled.push({ ...c, label: `OPTION ${labeled.length + 1}`, idx: labeled.length }); if (labeled.length >= 5) break; } }
    return labeled;
}

function renderPromoRoundTripSection(roundTripFlights, searchParams, tripAnalysis) {
    const section = document.createElement('div'); section.className = 'combo-section';
    const note = tripAnalysis?.winner === 'promo' && tripAnalysis?.savings_formatted
        ? ` <span class="combo-section-note">Cheaper than combo by ${escapeHTML(tripAnalysis.savings_formatted)}</span>`
        : '';
    section.innerHTML = `<div class="combo-section-title">PROMO ROUND-TRIPS${note}</div>`;
    const promos = [...roundTripFlights]
        .filter(f => f.price != null)
        .sort((a, b) => a.price - b.price)
        .slice(0, 3);
    promos.forEach((flight, i) => {
        const color = COMBO_COLORS[i % COMBO_COLORS.length];
        const via = (flight.layovers || []).map(l => l.code).filter(Boolean).join('+');
        const returnDate = flight.return_date || searchParams?.return_date || '';
        const card = document.createElement('div'); card.className = 'combo-card'; card.style.borderLeftColor = color.border; card.style.background = color.bg;
        card.innerHTML = `<div class="combo-card-header"><span class="combo-card-label" style="background:${color.label}">${i === 0 ? 'BEST PROMO' : `PROMO ${i + 1}`}</span><span class="combo-card-total" style="color:${color.text}">${formatPrice(flight)}</span></div><div class="combo-card-leg"><span class="combo-leg-dir" style="color:${color.text}">RT</span><span class="combo-leg-detail">${escapeHTML(flight.airline)} &middot; ${escapeHTML(flight.departure_airport)}&rarr;${escapeHTML(flight.arrival_airport)} ${flight.departure_date ? formatDate(flight.departure_date) : ''}${returnDate ? ` &rarr; ${formatDate(returnDate)}` : ''} &middot; ${escapeHTML(flight.duration)}${via ? ' via ' + via : ''}</span><span class="combo-leg-price">${formatPrice(flight)}</span><a href="${buildRoundTripLink(flight, returnDate)}" target="_blank" class="combo-leg-link">&#8599;</a></div>`;
        section.appendChild(card);
    });
    return section;
}

function renderComboAnalysis(combos, tripAnalysis) {
    const section = document.createElement('div'); section.className = 'combo-section';
    const note = tripAnalysis?.winner === 'combo' && tripAnalysis?.savings_formatted
        ? ` <span class="combo-section-note">Cheaper than promo by ${escapeHTML(tripAnalysis.savings_formatted)}</span>`
        : '';
    const countNote = tripAnalysis?.combo?.combos_checked
        ? `<span class="combo-section-note">Checked ${tripAnalysis.combo.combos_checked} combinations</span>`
        : '';
    section.innerHTML = `<div class="combo-section-title">COMBO FLIGHTS${note}${countNote}</div>`;
    combos.forEach((combo, i) => {
        const color = COMBO_COLORS[i % COMBO_COLORS.length]; const curr = combo.out.price_currency || '$';
        const outVia = (combo.out.layovers || []).map(l => l.code).filter(Boolean).join('+');
        const retVia = (combo.ret.layovers || []).map(l => l.code).filter(Boolean).join('+');
        const card = document.createElement('div'); card.className = 'combo-card'; card.style.borderLeftColor = color.border; card.style.background = color.bg;
        card.innerHTML = `<div class="combo-card-header"><span class="combo-card-label" style="background:${color.label}">${combo.label}</span><span class="combo-card-total" style="color:${color.text}">${curr}${combo.total.toLocaleString('en-US',{maximumFractionDigits:0})}</span></div><div class="combo-card-leg"><span class="combo-leg-dir" style="color:${color.text}">LEG 1</span><span class="combo-leg-detail">${escapeHTML(combo.out.airline)} &middot; ${escapeHTML(combo.out.departure_airport)}&rarr;${escapeHTML(combo.out.arrival_airport)} ${combo.out.departure_date?formatDate(combo.out.departure_date):''} &middot; ${escapeHTML(combo.out.duration)}${outVia?' via '+outVia:''}</span><span class="combo-leg-price">${formatPrice(combo.out)}</span><a href="${buildGoogleFlightsLink(combo.out)}" target="_blank" class="combo-leg-link">&#8599;</a></div><div class="combo-card-leg"><span class="combo-leg-dir" style="color:${color.text}">LEG 2</span><span class="combo-leg-detail">${escapeHTML(combo.ret.airline)} &middot; ${escapeHTML(combo.ret.departure_airport)}&rarr;${escapeHTML(combo.ret.arrival_airport)} ${combo.ret.departure_date?formatDate(combo.ret.departure_date):''} &middot; ${escapeHTML(combo.ret.duration)}${retVia?' via '+retVia:''}</span><span class="combo-leg-price">${formatPrice(combo.ret)}</span><a href="${buildGoogleFlightsLink(combo.ret)}" target="_blank" class="combo-leg-link">&#8599;</a></div>`;
        section.appendChild(card);
    });
    return section;
}

function renderBestDeal(deal) {
    const box = document.createElement('div'); box.className = 'best-deal-box';
    if (deal.type === 'promo_round_trip' && deal.promo) {
        const promo = deal.promo;
        const via = promo.stops === 0 ? 'Nonstop' : `${promo.stops} stop${promo.stops > 1 ? 's' : ''}${promo.via ? ' via ' + promo.via : ''}`;
        const comparison = deal.comparison?.savings_formatted ? `<div class="deal-footer"><span>Promo fare wins by <strong>${escapeHTML(deal.comparison.savings_formatted)}</strong> against the best combo flight.</span></div>` : `<div class="deal-footer"><span>Click <strong>Book</strong> to view on Google Flights and complete your booking.</span></div>`;
        box.innerHTML = `<div class="deal-header"><div class="deal-header-left"><span class="deal-badge">&#9992; ${deal.label}</span><span class="deal-total">${deal.total_formatted}</span><span class="deal-total-label">promo round-trip</span></div></div><div class="deal-legs"><div class="deal-leg"><span class="deal-leg-dir deal-leg-out">RT</span><div class="deal-leg-info"><div class="deal-leg-route">${escapeHTML(promo.route)} &middot; ${promo.departure_date ? formatDate(promo.departure_date) : ''}${promo.return_date ? ` &rarr; ${formatDate(promo.return_date)}` : ''}</div><div class="deal-leg-meta">${escapeHTML(promo.airline)} &middot; ${via} &middot; ${escapeHTML(promo.duration)}</div></div><span class="deal-leg-price">${promo.total_formatted}</span><a href="${promo.link}" target="_blank" class="deal-book-btn">Book</a></div></div>${comparison}`;
        return box;
    }
    const o = deal.outbound; const r = deal.return; const isRT = deal.type === 'round_trip' && r;
    const outStops = o.stops === 0 ? 'Nonstop' : `${o.stops} stop${o.stops > 1 ? 's' : ''}${o.via ? ' via ' + o.via : ''}`;
    let legsHTML = `<div class="deal-leg"><span class="deal-leg-dir deal-leg-out">OUT</span><div class="deal-leg-info"><div class="deal-leg-route">${escapeHTML(o.route)} &middot; ${o.date ? formatDate(o.date) : ''}</div><div class="deal-leg-meta">${escapeHTML(o.airline)} &middot; ${outStops} &middot; ${escapeHTML(o.duration)}</div></div><span class="deal-leg-price">${o.price_formatted}</span><a href="${o.link}" target="_blank" class="deal-book-btn">Book</a></div>`;
    if (isRT) { const retStops = r.stops === 0 ? 'Nonstop' : `${r.stops} stop${r.stops > 1 ? 's' : ''}${r.via ? ' via ' + r.via : ''}`; legsHTML += `<div class="deal-leg"><span class="deal-leg-dir deal-leg-ret">RET</span><div class="deal-leg-info"><div class="deal-leg-route">${escapeHTML(r.route)} &middot; ${r.date ? formatDate(r.date) : ''}</div><div class="deal-leg-meta">${escapeHTML(r.airline)} &middot; ${retStops} &middot; ${escapeHTML(r.duration)}</div></div><span class="deal-leg-price">${r.price_formatted}</span><a href="${r.link}" target="_blank" class="deal-book-btn">Book</a></div>`; }
    const comparison = deal.comparison?.savings_formatted ? `<div class="deal-footer"><span>Combo fare wins by <strong>${escapeHTML(deal.comparison.savings_formatted)}</strong> against the best promo round-trip.</span></div>` : `<div class="deal-footer"><span>Click <strong>Book</strong> to view on Google Flights and complete your booking.</span></div>`;
    box.innerHTML = `<div class="deal-header"><div class="deal-header-left"><span class="deal-badge">&#9992; ${deal.label}</span><span class="deal-total">${deal.total_formatted}</span>${isRT ? '<span class="deal-total-label">combo total</span>' : '<span class="deal-total-label">one-way</span>'}</div></div><div class="deal-legs">${legsHTML}</div>${comparison}`;
    return box;
}

function buildGoogleFlightsLink(f) { return `https://www.google.com/travel/flights?q=Flights+from+${f.departure_airport||''}+to+${f.arrival_airport||''}+on+${f.departure_date||''}+one+way+business+class`; }
function buildRoundTripLink(f, returnDate = '') { return `https://www.google.com/travel/flights?q=Flights+from+${f.departure_airport||''}+to+${f.arrival_airport||''}+on+${f.departure_date||''}${returnDate ? `+returning+${returnDate}` : ''}+business+class`; }
function groupByDate(flights) { const g = {}; flights.forEach(f => { const d = f.departure_date || 'unknown'; (g[d] = g[d] || []).push(f); }); return g; }
function parseDur(dur) { if (!dur) return 9999; let m = 0; const h = dur.match(/(\d+)\s*hr?/); const mn = dur.match(/(\d+)\s*min/); if (h) m += parseInt(h[1]) * 60; if (mn) m += parseInt(mn[1]); return m || 9999; }
function showTypingIndicator() { const msg = document.createElement('div'); msg.className = 'message bot'; msg.innerHTML = `<div class="message-avatar">&#9992;</div><div class="message-content"><div class="message-bubble"><div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div></div></div>`; chatContainer.appendChild(msg); scrollToBottom(); return msg; }
function scrollToBottom() { requestAnimationFrame(() => { chatContainer.scrollTop = chatContainer.scrollHeight; }); }
function formatPrice(f) { if (f.price == null) return 'Price N/A'; const c = f.price_currency || '$'; const v = f.price.toLocaleString('en-US', { maximumFractionDigits: 0 }); return (c === '$' || c === 'USD') ? '$' + v : c + ' ' + v; }
function formatDate(s) { try { const d = new Date(s + 'T00:00:00'); return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', weekday: 'short' }); } catch { return s; } }
function formatMessageText(t) { let h = escapeHTML(t); h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); h = h.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>'); h = h.replace(/\n/g, '<br>'); h = h.replace(/^[-*]\s+(.+)/gm, '&bull; $1'); return h; }
function escapeHTML(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }
