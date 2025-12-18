// State
let config = {};
let available = {};

// DOM elements
const voiceSelect = document.getElementById('voice');
const languageSelect = document.getElementById('language');
const modeSelect = document.getElementById('mode');
const styleSelect = document.getElementById('style');
const maxCharsInput = document.getElementById('max_chars');
const customStylesList = document.getElementById('custom-styles-list');
const newStyleName = document.getElementById('new-style-name');
const newStyleInstruction = document.getElementById('new-style-instruction');
const addStyleBtn = document.getElementById('add-style-btn');
const previewText = document.getElementById('preview-text');
const previewBtn = document.getElementById('preview-btn');
const saveBtn = document.getElementById('save-btn');
const resetBtn = document.getElementById('reset-btn');
const statusEl = document.getElementById('status');
const notification = document.getElementById('notification');

// API functions
async function fetchConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        config = data.config;
        available = data.available;
        updateUI();
    } catch (err) {
        showNotification('Failed to load config', 'error');
    }
}

async function saveConfig() {
    try {
        saveBtn.disabled = true;
        const newConfig = {
            voice: voiceSelect.value,
            language: languageSelect.value,
            mode: modeSelect.value,
            style: styleSelect.value,
            max_chars: parseInt(maxCharsInput.value),
            custom_styles: config.custom_styles || {}
        };

        const res = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newConfig)
        });

        const data = await res.json();
        if (data.status === 'ok') {
            config = newConfig;
            showNotification('Configuration saved', 'success');
        } else {
            showNotification(data.error || 'Save failed', 'error');
        }
    } catch (err) {
        showNotification('Failed to save config', 'error');
    } finally {
        saveBtn.disabled = false;
    }
}

async function preview() {
    const text = previewText.value.trim();
    if (!text) {
        showNotification('Enter text to preview', 'error');
        return;
    }

    try {
        previewBtn.disabled = true;
        previewBtn.querySelector('.btn-text').hidden = true;
        previewBtn.querySelector('.btn-loading').hidden = false;

        const useCurrentSession = document.getElementById('use-current-session')?.checked ?? true;

        const body = { text, use_current_session: useCurrentSession };

        // Only send config if not using current session
        if (!useCurrentSession) {
            body.config = {
                voice: voiceSelect.value,
                language: languageSelect.value,
                mode: modeSelect.value,
                style: styleSelect.value
            };
        }

        const res = await fetch('/api/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const data = await res.json();
        if (data.status !== 'ok') {
            showNotification(data.error || 'Preview failed', 'error');
        }
    } catch (err) {
        showNotification('Preview failed', 'error');
    } finally {
        previewBtn.disabled = false;
        previewBtn.querySelector('.btn-text').hidden = false;
        previewBtn.querySelector('.btn-loading').hidden = true;
    }
}

async function checkStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();

        statusEl.classList.remove('connected', 'disconnected');
        if (data.connected) {
            statusEl.classList.add('connected');
            statusEl.querySelector('.text').textContent = `Connected (${data.current_voice || 'N/A'})`;
        } else {
            statusEl.classList.add('disconnected');
            statusEl.querySelector('.text').textContent = 'Disconnected';
        }
    } catch (err) {
        statusEl.classList.remove('connected');
        statusEl.classList.add('disconnected');
        statusEl.querySelector('.text').textContent = 'Offline';
    }
}

// UI functions
function populateSelect(select, options, selected) {
    select.innerHTML = '';
    options.forEach(opt => {
        const option = document.createElement('option');
        option.value = opt;
        option.textContent = opt.charAt(0).toUpperCase() + opt.slice(1);
        if (opt === selected) option.selected = true;
        select.appendChild(option);
    });
}

function updateUI() {
    populateSelect(voiceSelect, available.voices || [], config.voice);
    populateSelect(languageSelect, available.languages || [], config.language);
    populateSelect(modeSelect, available.modes || [], config.mode);

    // Styles: built-in + custom
    const allStyles = [...(available.styles || []), ...Object.keys(config.custom_styles || {})];
    populateSelect(styleSelect, allStyles, config.style);

    maxCharsInput.value = config.max_chars || 1000;

    renderCustomStyles();
}

function renderCustomStyles() {
    customStylesList.innerHTML = '';
    const customStyles = config.custom_styles || {};

    Object.entries(customStyles).forEach(([name, instruction]) => {
        const item = document.createElement('div');
        item.className = 'custom-style-item';
        item.innerHTML = `
            <span class="name">${name}</span>
            <span class="instruction">${instruction}</span>
            <button class="delete-btn btn-secondary" data-name="${name}">Delete</button>
        `;
        customStylesList.appendChild(item);
    });

    // Add delete handlers
    customStylesList.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', () => deleteCustomStyle(btn.dataset.name));
    });
}

function addCustomStyle() {
    const name = newStyleName.value.trim();
    const instruction = newStyleInstruction.value.trim();

    if (!name || !instruction) {
        showNotification('Enter both name and instruction', 'error');
        return;
    }

    if (!config.custom_styles) config.custom_styles = {};
    config.custom_styles[name] = instruction;

    newStyleName.value = '';
    newStyleInstruction.value = '';

    updateUI();
    showNotification(`Style "${name}" added`, 'success');
}

function deleteCustomStyle(name) {
    if (config.custom_styles && config.custom_styles[name]) {
        delete config.custom_styles[name];

        // If deleted style was selected, switch to default
        if (config.style === name) {
            config.style = 'asmr';
        }

        updateUI();
        showNotification(`Style "${name}" deleted`, 'success');
    }
}

function resetToDefaults() {
    config = {
        mode: 'summary',
        voice: 'Aoede',
        style: 'asmr',
        language: 'russian',
        max_chars: 1000,
        custom_styles: {}
    };
    updateUI();
    showNotification('Reset to defaults (not saved yet)', 'success');
}

function showNotification(message, type = 'success') {
    notification.textContent = message;
    notification.className = `notification ${type}`;
    notification.hidden = false;

    setTimeout(() => {
        notification.hidden = true;
    }, 3000);
}

// Event listeners
saveBtn.addEventListener('click', saveConfig);
resetBtn.addEventListener('click', resetToDefaults);
previewBtn.addEventListener('click', preview);
addStyleBtn.addEventListener('click', addCustomStyle);

// Allow Enter to add custom style
newStyleInstruction.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') addCustomStyle();
});

// Init
fetchConfig();
checkStatus();
setInterval(checkStatus, 5000);
