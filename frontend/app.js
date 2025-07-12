// Configuration
const API_BASE_URL = 'https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api';

// Global state
let autoRefreshInterval = null;
let logTailInterval = null;
let currentTailJobId = null;
let logFontSizeIndex = 2; // Default to 'font-md'
let autoScrollEnabled = false;
let currentUser = null; // Store current user authentication data

// Global variables for instrumental selection
let currentJobId = null;
let selectedInstrumental = null;
let currentAudioPlayer = null;
let currentVisualizationMode = 'waveform'; // 'waveform' or 'spectrogram'
let visualizationCache = new Map(); // Cache for loaded waveforms/spectrograms

// Notification state tracking
let previousJobStates = new Map(); // Track previous states to detect changes
let notificationAudio = null; // Audio object for notification sounds
let originalPageTitle = document.title; // Store original title for flashing
let titleFlashInterval = null; // Interval for title flashing
let hasUnseenNotifications = false; // Track if there are unseen notifications
let notifiedJobStates = new Map(); // Track which job states we've already notified about

// Initialize notification system
function initializeNotificationSystem() {
    // Request notification permission on first load
    if ('Notification' in window && Notification.permission === 'default') {
        // Don't request immediately, wait for user interaction
        document.addEventListener('click', requestNotificationPermission, { once: true });
    }
    
    // Load previous notification state from localStorage
    loadNotificationState();
    
    // Initialize notification audio (we'll create the audio data URL)
    try {
        // Create a simple notification beep using Web Audio API
        createNotificationSound();
    } catch (error) {
        console.warn('Could not initialize notification audio:', error);
    }
    
    // Handle page visibility changes to stop notifications when user returns
    document.addEventListener('visibilitychange', handleVisibilityChange);
    
    // Handle window focus to clear notifications
    window.addEventListener('focus', handleWindowFocus);
    
    // Clean up old notification state periodically (older than 24 hours)
    cleanupOldNotificationState();
    
    console.log('📢 Notification system initialized');
}

function loadNotificationState() {
    try {
        const stored = localStorage.getItem('karaoke_notification_state');
        if (stored) {
            const parsed = JSON.parse(stored);
            // Convert array back to Map
            notifiedJobStates = new Map(parsed.notifiedJobStates || []);
            
            // Clean up entries older than 24 hours
            const twentyFourHoursAgo = Date.now() - (24 * 60 * 60 * 1000);
            for (const [key, timestamp] of notifiedJobStates.entries()) {
                if (timestamp < twentyFourHoursAgo) {
                    notifiedJobStates.delete(key);
                }
            }
        }
    } catch (error) {
        console.warn('Could not load notification state:', error);
        notifiedJobStates = new Map();
    }
}

function saveNotificationState() {
    try {
        const state = {
            notifiedJobStates: Array.from(notifiedJobStates.entries()),
            lastSaved: Date.now()
        };
        localStorage.setItem('karaoke_notification_state', JSON.stringify(state));
    } catch (error) {
        console.warn('Could not save notification state:', error);
    }
}

function cleanupOldNotificationState() {
    const twentyFourHoursAgo = Date.now() - (24 * 60 * 60 * 1000);
    let cleaned = false;
    
    for (const [key, timestamp] of notifiedJobStates.entries()) {
        if (timestamp < twentyFourHoursAgo) {
            notifiedJobStates.delete(key);
            cleaned = true;
        }
    }
    
    if (cleaned) {
        saveNotificationState();
    }
}

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission().then(permission => {
            if (permission === 'granted') {
                showInfo('Browser notifications enabled! You\'ll be alerted when jobs need your attention.');
            } else {
                showInfo('Browser notifications disabled. You can enable them in your browser settings.');
            }
        });
    }
}

function createNotificationSound() {
    // Create a simple beep sound using Web Audio API
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audioContext.createOscillator();
    const gainNode = audioContext.createGain();
    
    // Create audio buffer for notification sound
    const sampleRate = audioContext.sampleRate;
    const duration = 0.3; // 300ms
    const frameCount = sampleRate * duration;
    const audioBuffer = audioContext.createBuffer(1, frameCount, sampleRate);
    const channelData = audioBuffer.getChannelData(0);
    
    // Generate a pleasant notification sound (two tones)
    for (let i = 0; i < frameCount; i++) {
        const t = i / sampleRate;
        const envelope = Math.exp(-t * 3); // Exponential decay
        const tone1 = Math.sin(2 * Math.PI * 800 * t); // 800Hz
        const tone2 = Math.sin(2 * Math.PI * 1000 * t); // 1000Hz
        channelData[i] = (tone1 + tone2) * 0.1 * envelope; // Low volume
    }
    
    // Create audio source
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(gainNode);
    gainNode.connect(audioContext.destination);
    gainNode.gain.value = 0.3; // 30% volume
    
    // Store for later use
    notificationAudio = {
        context: audioContext,
        buffer: audioBuffer,
        play: function() {
            try {
                if (this.context.state === 'suspended') {
                    this.context.resume();
                }
                const source = this.context.createBufferSource();
                const gain = this.context.createGain();
                source.buffer = this.buffer;
                source.connect(gain);
                gain.connect(this.context.destination);
                gain.gain.value = 0.3;
                source.start();
            } catch (error) {
                console.warn('Could not play notification sound:', error);
            }
        }
    };
}

function checkForJobNotifications(jobs) {
    // States that require user action/attention
    const notificationStates = new Set([
        'awaiting_review',
        'ready_for_finalization', 
        'complete',
        'error'
    ]);
    
    const newNotifications = [];
    
    // Check each job for state changes
    Object.entries(jobs).forEach(([jobId, job]) => {
        const currentStatus = job.status;
        const previousStatus = previousJobStates.get(jobId);
        const notificationKey = `${jobId}-${currentStatus}`;
        
        // Update the stored state
        previousJobStates.set(jobId, currentStatus);
        
        // Check if this is a new state that requires notification
        if (currentStatus !== previousStatus && notificationStates.has(currentStatus)) {
            // Only notify for jobs that have actually changed state
            // (not initial loads where previousStatus is undefined)
            // AND we haven't already notified about this job status
            if (previousStatus !== undefined && !notifiedJobStates.has(notificationKey)) {
                newNotifications.push({
                    jobId,
                    job,
                    status: currentStatus,
                    previousStatus
                });
                
                // Mark this job status as notified
                notifiedJobStates.set(notificationKey, Date.now());
            }
        }
    });
    
    // Handle notifications
    if (newNotifications.length > 0) {
        handleJobNotifications(newNotifications);
        saveNotificationState(); // Persist the notification state
    }
}

function handleJobNotifications(notifications) {
    console.log('🔔 Handling job notifications:', notifications);
    console.log(`📊 Notification state - Page hidden: ${document.hidden}, Audio available: ${!!notificationAudio}`);
    
    // Play notification sound if page is not visible
    if (document.hidden && notificationAudio) {
        console.log('🔊 Playing notification sound');
        notificationAudio.play();
    }
    
    // Show browser notifications
    showBrowserNotifications(notifications);
    
    // Start title flashing if page is not visible
    if (document.hidden) {
        console.log('📋 Starting title flashing');
        startTitleFlashing();
        hasUnseenNotifications = true;
    }
    
    // Show in-app notifications
    notifications.forEach(notification => {
        console.log(`📱 Showing in-app notification for job ${notification.jobId}: ${notification.status}`);
        showInAppNotification(notification);
    });
}

function showBrowserNotifications(notifications) {
    if (!('Notification' in window) || Notification.permission !== 'granted') {
        return;
    }
    
    notifications.forEach(({ jobId, job, status }) => {
        const trackInfo = (job.artist && job.title) 
            ? `${job.artist} - ${job.title}` 
            : `Job ${jobId}`;
        
        let title, body, icon;
        
        switch (status) {
            case 'awaiting_review':
                title = '📝 Review Required';
                body = `${trackInfo} is ready for lyrics review`;
                icon = '📝';
                break;
            case 'ready_for_finalization':
                title = '🎵 Instrumental Selection Required';
                body = `${trackInfo} needs instrumental selection to complete`;
                icon = '🎵';
                break;
            case 'complete':
                title = '✅ Job Complete';
                body = `${trackInfo} has finished processing and is ready for download`;
                icon = '✅';
                break;
            case 'error':
                title = '❌ Job Failed';
                body = `${trackInfo} encountered an error and needs attention`;
                icon = '❌';
                break;
            default:
                return;
        }
        
        const notification = new Notification(title, {
            body: body,
            icon: `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">${icon}</text></svg>`,
            badge: `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🎤</text></svg>`,
            tag: `karaoke-job-${jobId}`, // Prevent duplicates
            requireInteraction: status !== 'complete', // Keep notification visible except for complete jobs
            silent: false
        });
        
        // Handle notification click
        notification.onclick = function() {
            window.focus();
            this.close();
        };
        
        // Auto-close non-critical notifications after 10 seconds
        if (status === 'complete') {
            setTimeout(() => {
                notification.close();
            }, 10000);
        }
    });
}

function showInAppNotification(notification) {
    const { jobId, job, status } = notification;
    const trackInfo = (job.artist && job.title) 
        ? `${job.artist} - ${job.title}` 
        : `Job ${jobId}`;
    
    let message, type;
    
    switch (status) {
        case 'awaiting_review':
            message = `🎤 ${trackInfo} is ready for lyrics review! Click to review.`;
            type = 'success';
            break;
        case 'ready_for_finalization':
            message = `🎵 ${trackInfo} needs instrumental selection to complete! Click to choose.`;
            type = 'success';
            break;
        case 'complete':
            message = `✅ ${trackInfo} is complete and ready for download!`;
            type = 'success';
            break;
        case 'error':
            message = `❌ ${trackInfo} encountered an error. Check the logs for details.`;
            type = 'error';
            break;
        default:
            return;
    }
    
    showNotification(message, type);
}

function startTitleFlashing() {
    if (titleFlashInterval) {
        return; // Already flashing
    }
    
    let isFlashed = false;
    titleFlashInterval = setInterval(() => {
        if (document.hidden) {
            document.title = isFlashed ? originalPageTitle : '🔔 Action Required - Karaoke Generator';
            isFlashed = !isFlashed;
        } else {
            stopTitleFlashing();
        }
    }, 1000);
}

function stopTitleFlashing() {
    if (titleFlashInterval) {
        clearInterval(titleFlashInterval);
        titleFlashInterval = null;
        document.title = originalPageTitle;
        hasUnseenNotifications = false;
    }
}

function handleVisibilityChange() {
    if (!document.hidden) {
        // Page became visible, stop notifications
        stopTitleFlashing();
        hasUnseenNotifications = false;
    }
}

function handleWindowFocus() {
    // Window got focus, stop all notification effects
    stopTitleFlashing();
    hasUnseenNotifications = false;
}

function testNotification() {
    if (!('Notification' in window) || Notification.permission !== 'granted') {
        showError('Browser notifications are not enabled. Please enable them first.');
        return;
    }
    
    // Play notification sound
    if (notificationAudio) {
        notificationAudio.play();
    }
    
    // Show test browser notification
    const testNotification = new Notification('🎤 Test Notification', {
        body: 'This is how you\'ll be notified when jobs need your attention!',
        icon: `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🔔</text></svg>`,
        badge: `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🎤</text></svg>`,
        tag: 'karaoke-test-notification',
        requireInteraction: false,
        silent: false
    });
    
    // Auto-close test notification after 5 seconds
    setTimeout(() => {
        testNotification.close();
    }, 5000);
    
    // Show in-app notification
    showSuccess('🔔 Test notification sent! You should hear a sound and see a browser notification.');
    
    // Test title flashing by temporarily hiding the page
    if (!document.hidden) {
        const originalTitle = document.title;
        document.title = '🔔 Test - Karaoke Generator';
        setTimeout(() => {
            document.title = originalTitle;
        }, 2000);
    }
}

// Authentication functions
function getAuthToken() {
    return localStorage.getItem('karaoke_auth_token');
}

function setAuthToken(token) {
    if (token) {
        localStorage.setItem('karaoke_auth_token', token);
    } else {
        localStorage.removeItem('karaoke_auth_token');
    }
}

function getAuthHeaders() {
    const token = getAuthToken();
    if (token) {
        return {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
        };
    }
    return {
        'Content-Type': 'application/json'
    };
}

async function authenticateUser() {
    const token = getAuthToken();
    if (!token) {
        showAuthSection();
        return false;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/auth/validate`, {
            method: 'POST',
            headers: getAuthHeaders()
        });

        if (response.ok) {
            const authData = await response.json();
            currentUser = authData;
            showMainApp(authData);
            return true;
        } else {
            // Token is invalid, clear it and show auth
            setAuthToken(null);
            currentUser = null;
            showAuthSection();
            return false;
        }
    } catch (error) {
        console.error('Authentication error:', error);
        showError('Authentication error: ' + error.message);
        showAuthSection();
        return false;
    }
}

function showAuthSection() {
    document.getElementById('auth-section').style.display = 'block';
    document.getElementById('main-app').style.display = 'none';
}

function showMainApp(authData) {
    document.getElementById('auth-section').style.display = 'none';
    document.getElementById('main-app').style.display = 'block';
    
    // Update user status bar
    updateUserStatusBar(authData);
    
    // Show admin panel if user is admin
    if (authData.admin_access) {
        document.getElementById('admin-panel').style.display = 'block';
    } else {
        document.getElementById('admin-panel').style.display = 'none';
    }
}

function updateUserStatusBar(authData) {
    const userTypeDisplay = document.getElementById('user-type-display');
    const userRemainingDisplay = document.getElementById('user-remaining-display');
    
    // Format user type for display
    const userTypeLabels = {
        'admin': '👑 Admin Access',
        'unlimited': '♾️ Unlimited Access', 
        'limited': '🎫 Limited Access',
        'stripe': '💳 Paid Access'
    };
    
    userTypeDisplay.textContent = userTypeLabels[authData.user_type] || authData.user_type;
    
    // Format remaining uses
    if (authData.remaining_uses === -1) {
        userRemainingDisplay.textContent = '';
    } else if (authData.remaining_uses === 0) {
        userRemainingDisplay.textContent = '⚠️ No uses remaining';
        userRemainingDisplay.classList.add('warning');
    } else {
        userRemainingDisplay.textContent = `${authData.remaining_uses} uses remaining`;
        userRemainingDisplay.classList.remove('warning');
    }
}

async function login(token) {
    try {
        const response = await fetch(`${API_BASE_URL}/auth/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ token: token })
        });

        const result = await response.json();

        if (result.success) {
            setAuthToken(result.access_token);
            currentUser = result;
            showMainApp(result);
            showSuccess('Authentication successful! Welcome to Karaoke Generator.');
            
            // Load initial data
            await loadJobs();
            
            return true;
        } else {
            showError('Authentication failed: ' + result.message);
            return false;
        }
    } catch (error) {
        console.error('Login error:', error);
        showError('Login failed: ' + error.message);
        return false;
    }
}

async function logout() {
    try {
        // Call logout endpoint if we have a token
        const token = getAuthToken();
        if (token) {
            await fetch(`${API_BASE_URL}/auth/logout`, {
                method: 'POST',
                headers: getAuthHeaders()
            });
        }
    } catch (error) {
        console.error('Logout error:', error);
    }
    
    // Clear local data
    setAuthToken(null);
    currentUser = null;
    
    // Stop auto-refresh and uncheck the checkbox
    stopAutoRefresh();
    const autoRefreshCheckbox = document.getElementById('auto-refresh');
    if (autoRefreshCheckbox) {
        autoRefreshCheckbox.checked = false;
    }
    
    // Show auth section
    showAuthSection();
    
    showInfo('Logged out successfully');
}

// User info modal functions
function showUserInfo() {
    if (!currentUser) return;
    
    const modal = document.getElementById('user-info-modal');
    const content = document.getElementById('user-info-content');
    
    const userTypeLabels = {
        'admin': 'Administrator',
        'unlimited': 'Unlimited Access', 
        'limited': 'Limited Access',
        'stripe': 'Paid Access'
    };
    
    const remainingText = currentUser.remaining_uses === -1 
        ? 'Unlimited' 
        : `${currentUser.remaining_uses} uses remaining`;
    
    // Check notification support and permission
    const notificationSupport = 'Notification' in window;
    const notificationPermission = notificationSupport ? Notification.permission : 'unsupported';
    const notificationStatus = notificationSupport 
        ? (notificationPermission === 'granted' ? '✅ Enabled' : 
           notificationPermission === 'denied' ? '❌ Blocked' : '⚠️ Not Set')
        : '❌ Not Supported';
    
    content.innerHTML = `
        <div class="user-info-grid">
            <div class="user-info-item">
                <label>Account Type:</label>
                <span>${userTypeLabels[currentUser.user_type] || currentUser.user_type}</span>
            </div>
            <div class="user-info-item">
                <label>Remaining Uses:</label>
                <span>${remainingText}</span>
            </div>
            <div class="user-info-item">
                <label>Admin Access:</label>
                <span>${currentUser.admin_access ? 'Yes' : 'No'}</span>
            </div>
            <div class="user-info-item">
                <label>Status:</label>
                <span>${currentUser.message}</span>
            </div>
            <div class="user-info-item">
                <label>Notifications:</label>
                <span>${notificationStatus}</span>
            </div>
        </div>
        
        <div class="notification-controls">
            <h4>🔔 Notification Settings</h4>
            <div class="notification-settings">
                ${notificationSupport ? `
                    <div class="notification-setting">
                        <label>Browser Notifications:</label>
                        <div class="notification-actions">
                            ${notificationPermission === 'default' ? 
                                '<button onclick="requestNotificationPermission()" class="btn btn-primary btn-sm">Enable Notifications</button>' :
                                notificationPermission === 'granted' ?
                                '<span class="notification-enabled">✅ Enabled</span>' :
                                '<span class="notification-blocked">❌ Blocked (check browser settings)</span>'
                            }
                        </div>
                    </div>
                    <div class="notification-setting">
                        <label>Test Notification:</label>
                        <div class="notification-actions">
                            <button onclick="testNotification()" class="btn btn-secondary btn-sm" ${notificationPermission !== 'granted' ? 'disabled' : ''}>
                                🔔 Test Sound & Notification
                            </button>
                        </div>
                    </div>
                ` : `
                    <div class="notification-unsupported">
                        <p>❌ Browser notifications are not supported in this browser.</p>
                        <p>You'll still receive in-app notifications when jobs need attention.</p>
                    </div>
                `}
            </div>
        </div>
    `;
    
    modal.style.display = 'flex';
}

function closeUserInfoModal() {
    document.getElementById('user-info-modal').style.display = 'none';
}

// Token management functions (admin only)
async function showTokenManagement() {
    if (!currentUser || !currentUser.admin_access) {
        showError('Admin access required');
        return;
    }
    
    const modal = document.getElementById('token-management-modal');
    const content = document.getElementById('token-management-content');
    
    modal.style.display = 'flex';
    content.innerHTML = '<div class="token-loading">Loading tokens...</div>';
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/tokens/list`);
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const result = await response.json();
            displayTokenManagement(result.tokens);
        } else {
            const error = await response.json();
            content.innerHTML = `<div class="error">Error loading tokens: ${error.message}</div>`;
        }
    } catch (error) {
        console.error('Error loading tokens:', error);
        content.innerHTML = `<div class="error">Error loading tokens: ${error.message}</div>`;
    }
}

function displayTokenManagement(tokens) {
    const content = document.getElementById('token-management-content');
    
    let html = `
        <div class="token-management">
            <div class="token-create-section">
                <h4>Create New Token</h4>
                <form id="create-token-form" onsubmit="createToken(event)">
                    <div class="form-row">
                        <div class="form-group">
                            <label for="token-type">Type:</label>
                            <select id="token-type" required>
                                <option value="unlimited">Unlimited Access</option>
                                <option value="limited">Limited Access</option>
                                <option value="stripe">Stripe Payment</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="token-value">Token:</label>
                            <input type="text" id="token-value" placeholder="Enter token value" required>
                        </div>
                        <div class="form-group">
                            <label for="token-max-uses">Max Uses:</label>
                            <input type="number" id="token-max-uses" placeholder="Leave empty for unlimited" min="1">
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="token-description">Description:</label>
                        <input type="text" id="token-description" placeholder="Optional description">
                    </div>
                    <button type="submit" class="btn btn-primary">Create Token</button>
                </form>
            </div>
            
            <div class="token-list-section">
                <h4>Existing Tokens (${tokens.length})</h4>
                <div class="token-list">
    `;
    
    if (tokens.length === 0) {
        html += '<p class="no-tokens">No tokens found.</p>';
    } else {
        tokens.forEach(token => {
            const typeLabels = {
                'unlimited': 'Unlimited',
                'limited': 'Limited',
                'stripe': 'Stripe',
                'admin': 'Admin'
            };
            
            const usageText = token.max_uses === -1 
                ? `${token.current_uses} uses`
                : `${token.current_uses}/${token.max_uses} uses`;
            
            const statusClass = token.active ? 'active' : 'revoked';
            const statusText = token.active ? 'Active' : 'Revoked';
            
            html += `
                <div class="token-item ${statusClass}">
                    <div class="token-info">
                        <div class="token-header">
                            <span class="token-value">${token.token}</span>
                            <span class="token-type">${typeLabels[token.type] || token.type}</span>
                            <span class="token-status ${statusClass}">${statusText}</span>
                        </div>
                        <div class="token-details">
                            <span class="token-usage">${usageText}</span>
                            <span class="token-jobs">${token.jobs_created} jobs</span>
                            ${token.description ? `<span class="token-description">${token.description}</span>` : ''}
                        </div>
                        ${token.last_used ? `<div class="token-last-used">Last used: ${formatTimestamp(new Date(token.last_used * 1000).toISOString())}</div>` : ''}
                    </div>
                    <div class="token-actions">
                        ${token.active ? `<button onclick="revokeToken('${token.token}')" class="btn btn-danger btn-sm">Revoke</button>` : ''}
                    </div>
                </div>
            `;
        });
    }
    
    html += `
                </div>
            </div>
        </div>
    `;
    
    content.innerHTML = html;
}

async function createToken(event) {
    event.preventDefault();
    
    const tokenType = document.getElementById('token-type').value;
    const tokenValue = document.getElementById('token-value').value.trim();
    const maxUses = document.getElementById('token-max-uses').value;
    const description = document.getElementById('token-description').value.trim();
    
    if (!tokenValue) {
        showError('Token value is required');
        return;
    }
    
    try {
        const requestData = {
            token_type: tokenType,
            token_value: tokenValue,
            description: description || null
        };
        
        if (maxUses) {
            requestData.max_uses = parseInt(maxUses);
        }
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/tokens/create`, {
            method: 'POST',
            body: JSON.stringify(requestData)
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(result.message);
            // Clear form first before refreshing (which recreates the modal content)
            const form = document.getElementById('create-token-form');
            if (form) {
                form.reset();
            }
            // Refresh token list
            showTokenManagement();
        } else {
            showError('Error creating token: ' + result.message);
        }
    } catch (error) {
        console.error('Error creating token:', error);
        showError('Error creating token: ' + error.message);
    }
}

async function revokeToken(tokenValue) {
    if (!confirm(`Are you sure you want to revoke token "${tokenValue}"?`)) {
        return;
    }
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/tokens/${encodeURIComponent(tokenValue)}/revoke`, {
            method: 'POST'
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(result.message);
            // Refresh token list
            showTokenManagement();
        } else {
            showError('Error revoking token: ' + result.message);
        }
    } catch (error) {
        console.error('Error revoking token:', error);
        showError('Error revoking token: ' + error.message);
    }
}

function closeTokenManagementModal() {
    document.getElementById('token-management-modal').style.display = 'none';
}

// Cookie management functions (admin only)
async function showCookieManagement() {
    if (!currentUser || !currentUser.admin_access) {
        showError('Admin access required');
        return;
    }
    
    const modal = document.getElementById('cookie-management-modal');
    const content = document.getElementById('cookie-management-content');
    
    modal.style.display = 'flex';
    content.innerHTML = '<div class="cookie-loading">Loading cookie settings...</div>';
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cookies/status`);
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const result = await response.json();
            displayCookieManagement(result);
        } else {
            const error = await response.json();
            content.innerHTML = `<div class="error">Error loading cookie settings: ${error.message}</div>`;
        }
    } catch (error) {
        console.error('Error loading cookie settings:', error);
        content.innerHTML = `<div class="error">Error loading cookie settings: ${error.message}</div>`;
    }
}

function displayCookieManagement(cookieData) {
    const content = document.getElementById('cookie-management-content');
    
    const hasActiveCookies = cookieData.has_cookies;
    const lastUpdated = cookieData.last_updated;
    const isExpired = cookieData.is_expired;
    
    let html = `
        <div class="cookie-management">
            <div class="cookie-status-section">
                <h4>Current Cookie Status</h4>
                <div class="cookie-status-info">
                    <div class="status-indicator ${hasActiveCookies ? (isExpired ? 'warning' : 'active') : 'inactive'}">
                        ${hasActiveCookies ? (isExpired ? '⚠️ Expired' : '✅ Active') : '❌ No Cookies Set'}
                    </div>
                    ${lastUpdated ? `<div class="last-updated">Last updated: ${formatTimestamp(lastUpdated)}</div>` : ''}
                </div>
                
                ${hasActiveCookies && isExpired ? `
                    <div class="cookie-warning">
                        <p><strong>⚠️ Warning:</strong> The current cookies may be expired. YouTube jobs might fail.</p>
                        <p>Please update the cookies below to ensure reliable YouTube access.</p>
                    </div>
                ` : ''}
            </div>
            
            <div class="cookie-update-section">
                <h4>Update YouTube Cookies</h4>
                <form id="update-cookies-form" onsubmit="updateCookies(event)">
                    <div class="form-group">
                        <label for="cookie-data">YouTube Cookies</label>
                        <textarea id="cookie-data" class="form-control" rows="8" 
                                placeholder="Paste YouTube cookies here...&#10;&#10;How to get cookies:&#10;1. Visit youtube.com in your browser&#10;2. Open Developer Tools (F12)&#10;3. Go to Application → Cookies → https://www.youtube.com&#10;4. Copy all cookie data and paste here" required></textarea>
                        <small class="help-text">These cookies will be used for all YouTube jobs to bypass bot detection</small>
                    </div>
                    
                    <div class="form-actions">
                        <button type="submit" class="btn btn-primary">💾 Update Cookies</button>
                        ${hasActiveCookies ? '<button type="button" onclick="testCookies()" class="btn btn-secondary">🧪 Test Current Cookies</button>' : ''}
                        ${hasActiveCookies ? '<button type="button" onclick="deleteCookies()" class="btn btn-danger">🗑️ Delete Cookies</button>' : ''}
                    </div>
                </form>
            </div>
            
            <div class="cookie-help-section">
                <h4>Help & Instructions</h4>
                <div class="cookie-instructions">
                    <p><strong>Why are cookies needed?</strong></p>
                    <p>YouTube has bot detection that blocks server requests. Using browser cookies allows the system to appear as a regular user.</p>
                    
                    <p><strong>How to extract cookies:</strong></p>
                    <ol>
                        <li>Open <a href="https://www.youtube.com" target="_blank">YouTube</a> in your browser</li>
                        <li>Make sure you're signed in to your Google account</li>
                        <li>Open Developer Tools (Press F12)</li>
                        <li>Go to the <strong>Application</strong> tab</li>
                        <li>In the sidebar, expand <strong>Cookies</strong></li>
                        <li>Click on <strong>https://www.youtube.com</strong></li>
                        <li>Select all cookies (Ctrl+A) and copy them</li>
                        <li>Paste the cookie data in the textarea above</li>
                    </ol>
                    
                    <p><strong>Security:</strong></p>
                    <ul>
                        <li>Cookies are stored securely and used only for YouTube access</li>
                        <li>Only admin users can view or modify cookies</li>
                        <li>Cookies are automatically used for all YouTube jobs</li>
                    </ul>
                </div>
            </div>
        </div>
    `;
    
    content.innerHTML = html;
}

async function updateCookies(event) {
    event.preventDefault();
    
    const cookieData = document.getElementById('cookie-data').value.trim();
    
    if (!cookieData) {
        showError('Please provide cookie data');
        return;
    }
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cookies/update`, {
            method: 'POST',
            body: JSON.stringify({ cookies: cookieData })
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(result.message);
            // Clear form and refresh status
            document.getElementById('cookie-data').value = '';
            showCookieManagement();
        } else {
            showError('Error updating cookies: ' + result.message);
        }
    } catch (error) {
        console.error('Error updating cookies:', error);
        showError('Error updating cookies: ' + error.message);
    }
}

async function testCookies() {
    try {
        showInfo('Testing cookies with YouTube...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cookies/test`, {
            method: 'POST'
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(`Cookies test successful: ${result.message}`);
        } else {
            showError(`Cookies test failed: ${result.message}`);
        }
    } catch (error) {
        console.error('Error testing cookies:', error);
        showError('Error testing cookies: ' + error.message);
    }
}

async function deleteCookies() {
    if (!confirm('Are you sure you want to delete the stored YouTube cookies? This will cause YouTube jobs to fail until new cookies are provided.')) {
        return;
    }
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cookies/delete`, {
            method: 'DELETE'
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(result.message);
            // Refresh cookie management
            showCookieManagement();
        } else {
            showError('Error deleting cookies: ' + result.message);
        }
    } catch (error) {
        console.error('Error deleting cookies:', error);
        showError('Error deleting cookies: ' + error.message);
    }
}

function closeCookieManagementModal() {
    document.getElementById('cookie-management-modal').style.display = 'none';
}

// Delivery Message Template Management Functions (Admin Only)
async function showDeliveryMessageTemplate() {
    if (!currentUser || !currentUser.admin_access) {
        showError('Admin access required');
        return;
    }
    
    const modal = document.getElementById('delivery-message-template-modal');
    const content = document.getElementById('delivery-message-template-content');
    
    modal.style.display = 'flex';
    content.innerHTML = '<div class="delivery-template-loading">Loading template...</div>';
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/delivery-message/template`);
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const result = await response.json();
            displayDeliveryMessageTemplate(result);
        } else {
            const error = await response.json();
            content.innerHTML = `<div class="error">Error loading template: ${error.message}</div>`;
        }
    } catch (error) {
        console.error('Error loading delivery message template:', error);
        content.innerHTML = `<div class="error">Error loading template: ${error.message}</div>`;
    }
}

function displayDeliveryMessageTemplate(templateData) {
    const content = document.getElementById('delivery-message-template-content');
    
    const lastUpdated = templateData.updated_at 
        ? new Date(templateData.updated_at * 1000).toLocaleString()
        : 'Never';
    
    const updatedBy = templateData.updated_by || 'Unknown';
    
    let html = `
        <div class="delivery-template-management">
            <div class="template-info-section">
                <h4>📧 Email Delivery Template</h4>
                <div class="template-info">
                    <p>This template is used when copying delivery messages for completed jobs. Available template variables:</p>
                    <ul>
                        <li><code>{youtube_url}</code> - The YouTube video URL</li>
                        <li><code>{dropbox_url}</code> - The Dropbox folder sharing link</li>
                        <li><code>[NAME]</code> - Placeholder for customer name (manual replacement)</li>
                    </ul>
                    <p class="template-status">
                        <strong>Last updated:</strong> ${lastUpdated} by ${updatedBy}
                    </p>
                </div>
            </div>
            
            <div class="template-edit-section">
                <form id="delivery-template-form" onsubmit="updateDeliveryMessageTemplate(event)">
                    <div class="form-group">
                        <label for="delivery-template-text">Template Text</label>
                        <textarea id="delivery-template-text" class="form-control template-textarea" rows="20" required>${templateData.template}</textarea>
                        <small class="help-text">Use {youtube_url} and {dropbox_url} for automatic substitution</small>
                    </div>
                    
                    <div class="template-actions">
                        <button type="submit" class="btn btn-primary">
                            💾 Save Template
                        </button>
                        <button type="button" onclick="testDeliveryTemplate()" class="btn btn-secondary">
                            🧪 Preview Template
                        </button>
                        <button type="button" onclick="closeDeliveryMessageTemplateModal()" class="btn btn-secondary">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>
            
            <div id="template-preview-section" class="template-preview-section" style="display: none;">
                <h4>📋 Template Preview</h4>
                <div id="template-preview-content" class="template-preview-content"></div>
            </div>
        </div>
    `;
    
    content.innerHTML = html;
}

async function updateDeliveryMessageTemplate(event) {
    event.preventDefault();
    
    const templateText = document.getElementById('delivery-template-text').value.trim();
    
    if (!templateText) {
        showError('Template text is required');
        return;
    }
    
    try {
        showInfo('Saving template...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/delivery-message/template`, {
            method: 'POST',
            body: JSON.stringify({ template: templateText })
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(result.message);
            // Refresh the display to show updated timestamp
            setTimeout(() => showDeliveryMessageTemplate(), 1000);
        } else {
            showError('Error saving template: ' + result.message);
        }
    } catch (error) {
        console.error('Error saving delivery message template:', error);
        showError('Error saving template: ' + error.message);
    }
}

function testDeliveryTemplate() {
    const templateText = document.getElementById('delivery-template-text').value;
    const previewSection = document.getElementById('template-preview-section');
    const previewContent = document.getElementById('template-preview-content');
    
    // Create test data
    const testData = {
        youtube_url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        dropbox_url: 'https://www.dropbox.com/sh/example123/AABbCc?dl=0'
    };
    
    // Template the message
    const templatedMessage = templateDeliveryMessage(templateText, testData.youtube_url, testData.dropbox_url);
    
    // Show preview
    previewContent.innerHTML = `<pre>${escapeHtml(templatedMessage)}</pre>`;
    previewSection.style.display = 'block';
        
    showInfo('Template preview generated with test data');
}

function closeDeliveryMessageTemplateModal() {
    document.getElementById('delivery-message-template-modal').style.display = 'none';
}

// Delivery Message Templating Functions
function templateDeliveryMessage(template, youtubeUrl, dropboxUrl) {
    /**
     * Template a delivery message with YouTube and Dropbox URLs
     */
    if (!template) {
        return '';
    }
    
    // Replace template variables
    let templatedMessage = template;
    
    // Replace YouTube URL
    if (youtubeUrl) {
        templatedMessage = templatedMessage.replace(/{youtube_url}/g, youtubeUrl);
    } else {
        templatedMessage = templatedMessage.replace(/{youtube_url}/g, '[YouTube URL not available]');
    }
    
    // Replace Dropbox URL
    if (dropboxUrl) {
        templatedMessage = templatedMessage.replace(/{dropbox_url}/g, dropboxUrl);
    } else {
        templatedMessage = templatedMessage.replace(/{dropbox_url}/g, '[Dropbox URL not available]');
    }
    
    return templatedMessage;
}

async function copyDeliveryMessage(jobId) {
    /**
     * Copy a templated delivery message for a completed job to the clipboard
     */
    try {
        showInfo('Preparing delivery message...');
        
        // Get the job data
        const response = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}`);
        if (!response) return; // Auth failed, already handled
        
        const jobData = await response.json();
        
        // Get the current template
        const templateResponse = await authenticatedFetch(`${API_BASE_URL}/admin/delivery-message/template`);
        if (!templateResponse) return; // Auth failed, already handled
        
        const templateData = await templateResponse.json();
        
        if (!templateData.success) {
            showError('Error loading delivery message template');
            return;
        }
        
        // Extract URLs from job data
        const youtubeUrl = jobData.youtube_url || null;
        const dropboxUrl = jobData.brand_code_dir_sharing_link || null;
        
        // Template the message
        const templatedMessage = templateDeliveryMessage(templateData.template, youtubeUrl, dropboxUrl);
        
        // Copy to clipboard
        try {
            await navigator.clipboard.writeText(templatedMessage);
            showSuccess('Delivery message copied to clipboard!');
        } catch (clipboardError) {
            console.warn('Modern clipboard API failed, trying fallback:', clipboardError);
            
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = templatedMessage;
            textArea.style.position = 'fixed';
            textArea.style.left = '-999999px';
            textArea.style.top = '-999999px';
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            
            try {
                const successful = document.execCommand('copy');
                document.body.removeChild(textArea);
                
                if (successful) {
                    showSuccess('Delivery message copied to clipboard! (fallback method)');
                } else {
                    showError('Failed to copy to clipboard');
                }
            } catch (fallbackError) {
                console.error('Fallback copy failed:', fallbackError);
                document.body.removeChild(textArea);
                
                // Show the message in a modal as last resort
                showDeliveryMessagePopup(templatedMessage);
            }
        }
        
    } catch (error) {
        console.error('Error copying delivery message:', error);
        showError('Error preparing delivery message: ' + error.message);
    }
}

function showDeliveryMessagePopup(message) {
    /**
     * Show delivery message in a popup if clipboard fails
     */
    const modalHtml = `
        <div id="delivery-message-popup" class="modal">
            <div class="modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">📧 Delivery Message</h3>
                    <div class="modal-controls">
                        <button onclick="closeDeliveryMessagePopup()" class="modal-close">✕</button>
                    </div>
                </div>
                <div class="modal-body">
                    <p>Copy failed. Please manually copy the message below:</p>
                    <textarea class="delivery-message-popup-text" readonly onclick="this.select()">${escapeHtml(message)}</textarea>
                    <div class="popup-actions">
                        <button onclick="closeDeliveryMessagePopup()" class="btn btn-primary">Close</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Remove existing popup
    const existingPopup = document.getElementById('delivery-message-popup');
    if (existingPopup) {
        existingPopup.remove();
    }
    
    // Add popup to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Show popup
    const popup = document.getElementById('delivery-message-popup');
    popup.style.display = 'flex';
    
    // Auto-select text
    setTimeout(() => {
        const textarea = popup.querySelector('.delivery-message-popup-text');
        if (textarea) {
            textarea.select();
        }
    }, 100);
}

function closeDeliveryMessagePopup() {
    const popup = document.getElementById('delivery-message-popup');
    if (popup) {
        popup.remove();
    }
}

// Update all API calls to include authentication headers
async function authenticatedFetch(url, options = {}) {
    const headers = {
        ...getAuthHeaders(),
        ...options.headers
    };
    
    const response = await fetch(url, {
        ...options,
        headers
    });
    
    // If we get a 401, the token has expired - redirect to login
    if (response.status === 401) {
        setAuthToken(null);
        currentUser = null;
        
        // Stop auto-refresh and uncheck the checkbox
        stopAutoRefresh();
        const autoRefreshCheckbox = document.getElementById('auto-refresh');
        if (autoRefreshCheckbox) {
            autoRefreshCheckbox.checked = false;
        }
        
        showAuthSection();
        showError('Session expired. Please log in again.');
        return null;
    }
    
    return response;
}

// Initialize the application
document.addEventListener('DOMContentLoaded', function() {
    // Initialize the form to file upload mode (ensures proper required field setup)
    switchInputMode('file');
    
    // Initialize notification system
    initializeNotificationSystem();
    
    // Debug timezone information for troubleshooting timestamp issues
    // console.log('🌍 Timezone Debug Info:', {
    //     userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    //     timezoneOffset: new Date().getTimezoneOffset(),
    //     timezoneOffsetHours: new Date().getTimezoneOffset() / 60,
    //     localTime: new Date().toISOString(),
    //     localTimeString: new Date().toString(),
    //     localDateString: new Date().toLocaleDateString(),
    //     localTimeStringFormatted: new Date().toLocaleString(),
    //     sampleUTCParsing: parseServerTime('2024-01-01T12:00:00').toString(),
    //     userAgent: navigator.userAgent.substring(0, 100)
    // });
    
    // Initialize authentication
    authenticateUser().then(isAuthenticated => {
        if (isAuthenticated) {
            loadJobs();
            
            // Only start auto-refresh after successful authentication
            const autoRefreshCheckbox = document.getElementById('auto-refresh');
            if (autoRefreshCheckbox && autoRefreshCheckbox.checked) {
                startAutoRefresh();
            }
        }
    });
    
    // Handle auth form submission
    const authForm = document.getElementById('auth-form');
    if (authForm) {
        authForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const token = document.getElementById('access-token').value.trim();
            if (token) {
                await login(token);
            }
        });
    }
    
    // Auto-refresh checkbox handler
    const autoRefreshCheckbox = document.getElementById('auto-refresh');
    if (autoRefreshCheckbox) {
        autoRefreshCheckbox.addEventListener('change', function() {
            // Only allow auto-refresh if user is authenticated
            if (!currentUser) {
                this.checked = false;
                showError('Please log in to use auto-refresh');
                return;
            }
            
            if (this.checked) {
                startAutoRefresh();
                showInfo('Auto-refresh enabled - jobs will update every 5 seconds');
            } else {
                stopAutoRefresh();
                showInfo('Auto-refresh disabled');
            }
        });
    }
    
    // Handle form submission
    const jobForm = document.getElementById('job-form');
    if (jobForm) {
        jobForm.addEventListener('submit', function(e) {
            e.preventDefault();
            submitJob();
        });
    }
    
    // Handle modal close on outside click
    const modal = document.getElementById('log-tail-modal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                closeLogTailModal();
            }
        });
    }
    
    const cacheModal = document.getElementById('cache-stats-modal');
    if (cacheModal) {
        cacheModal.addEventListener('click', function(e) {
            if (e.target === cacheModal) {
                closeCacheStatsModal();
            }
        });
    }
    
    const userInfoModal = document.getElementById('user-info-modal');
    if (userInfoModal) {
        userInfoModal.addEventListener('click', function(e) {
            if (e.target === userInfoModal) {
                closeUserInfoModal();
            }
        });
    }
    
    const tokenModal = document.getElementById('token-management-modal');
    if (tokenModal) {
        tokenModal.addEventListener('click', function(e) {
            if (e.target === tokenModal) {
                closeTokenManagementModal();
            }
        });
    }
    
    const cookieModal = document.getElementById('cookie-management-modal');
    if (cookieModal) {
        cookieModal.addEventListener('click', function(e) {
            if (e.target === cookieModal) {
                closeCookieManagementModal();
            }
        });
    }
    
    const instrumentalModal = document.getElementById('instrumental-selection-modal');
    if (instrumentalModal) {
        instrumentalModal.addEventListener('click', function(e) {
            if (e.target === instrumentalModal) {
                closeInstrumentalSelectionModal();
            }
        });
    }
    
    const cloneJobModal = document.getElementById('clone-job-modal');
    if (cloneJobModal) {
        cloneJobModal.addEventListener('click', function(e) {
            if (e.target === cloneJobModal) {
                closeCloneJobModal();
            }
        });
    }
    
    // Keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        // Escape key closes modals
        if (e.key === 'Escape') {
            closeLogTailModal();
            closeCacheStatsModal();
            closeFilesModal();
            closeVideoPreview();
            closeAudioPreview();
            closeTimelineModal();
            closeUserInfoModal();
            closeTokenManagementModal();
            closeCookieManagementModal();
            closeInstrumentalSelectionModal();
            closeCloneJobModal();
        }
    });
});

function startAutoRefresh() {
    // Don't start auto-refresh if user is not authenticated
    if (!currentUser) {
        console.log('Auto-refresh not started: user not authenticated');
        const autoRefreshCheckbox = document.getElementById('auto-refresh');
        if (autoRefreshCheckbox) {
            autoRefreshCheckbox.checked = false;
        }
        return;
    }
    
    if (autoRefreshInterval) {
        console.log('Auto-refresh already running');
        return; // Already running
    }
    
    console.log('Starting auto-refresh (5s intervals)');
    
    // Add visual indicator
    const autoRefreshCheckbox = document.getElementById('auto-refresh');
    if (autoRefreshCheckbox) {
        autoRefreshCheckbox.parentElement.classList.add('auto-refresh-active');
    }
    
    autoRefreshInterval = setInterval(() => {
        try {
            // Check if user is still authenticated before refreshing
            if (!currentUser) {
                console.log('Auto-refresh: Stopping due to lost authentication');
                stopAutoRefresh();
                return;
            }
            
            // Only refresh if not tailing logs (to avoid conflicts)
            if (!currentTailJobId) {
                console.log('Auto-refresh: Loading jobs...');
                loadJobsWithoutScroll();
            } else {
                console.log('Auto-refresh: Skipping due to active log tail');
            }
        } catch (error) {
            console.error('Auto-refresh error:', error);
            // Don't stop auto-refresh on errors, just log them
        }
    }, 5000); // 5 second refresh
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        console.log('Stopping auto-refresh');
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
        
        // Remove visual indicator
        const autoRefreshCheckbox = document.getElementById('auto-refresh');
        if (autoRefreshCheckbox) {
            autoRefreshCheckbox.parentElement.classList.remove('auto-refresh-active');
        }
    }
}

function loadJobsWithoutScroll() {
    return loadJobs().catch(error => {
        console.error('Error in loadJobsWithoutScroll:', error);
        // Don't throw the error to prevent auto-refresh from stopping
        showError('Auto-refresh failed: ' + error.message);
    });
}

async function loadJobs() {
    try {
        // console.log('Loading jobs from API...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/jobs`);
        
        if (!response) return null; // Auth failed, already handled
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const jobs = await response.json();
        // console.log(`Loaded ${Object.keys(jobs).length} jobs`);
        
        // Check for job state changes that require notifications
        checkForJobNotifications(jobs);
        
        updateJobsList(jobs);
        updateStats(jobs);
        
        return jobs; // Return jobs for further use
        
    } catch (error) {
        console.error('Error loading jobs:', error);
        showError('Failed to load jobs: ' + error.message);
        
        // Still return null but don't break the calling code
        return null;
    }
}

// Removed: checkAndUpdateTimeouts() - now using Modal's built-in timeout handling

function updateJobsList(jobs) {
    const jobsList = document.getElementById('jobs-list');
    if (!jobsList) return;
    
    if (Object.keys(jobs).length === 0) {
        jobsList.innerHTML = '<p class="no-jobs">No jobs found. Submit a job above to get started!</p>';
        return;
    }
    
    const sortedJobs = Object.entries(jobs).sort((a, b) => {
        const timeA = parseServerTime(a[1].created_at || 0);
        const timeB = parseServerTime(b[1].created_at || 0);
        return timeB - timeA; // Most recent first
    });
    
    jobsList.innerHTML = sortedJobs.map(([jobId, job]) => {
        return createJobHTML(jobId, job);
    }).join('');
}

function updateStats(jobs) {
    const stats = {
        total: 0,
        processing: 0,
        awaiting_review: 0,
        complete: 0,
        error: 0
    };
    
    Object.values(jobs).forEach(job => {
        stats.total++;
        const status = job.status || 'unknown';
        if (['queued', 'processing_audio', 'transcribing', 'rendering', 'processing', 'finalizing'].includes(status)) {
            stats.processing++;
        } else if (status === 'awaiting_review') {
            stats.awaiting_review++;
        } else if (status === 'complete') {
            stats.complete++;
        } else if (['error', 'timeout'].includes(status)) {
            stats.error++;
        }
    });
    
    // Update stat cards
    document.getElementById('stat-total').textContent = stats.total;
    document.getElementById('stat-processing').textContent = stats.processing;
    document.getElementById('stat-awaiting-review').textContent = stats.awaiting_review;
    document.getElementById('stat-complete').textContent = stats.complete;
    document.getElementById('stat-errors').textContent = stats.error;
}

function createJobHTML(jobId, job) {
    const status = job.status || 'unknown';
    const progress = job.progress || 0;
    const timestamp = job.created_at ? formatTimestamp(job.created_at) : 'Unknown';
    const duration = formatDurationWithStatus(job);
    
    // Format track info for display with brand code prefix
    let trackInfo;
    if (job.artist && job.title) {
        const brandPrefix = job.brand_code ? `${job.brand_code}: ` : '';
        trackInfo = `${brandPrefix}${job.artist} - ${job.title}`;
    } else {
        trackInfo = job.url ? 'URL Processing' : 'Unknown Track';
    }
    
    // Get timeline information
    const timelineInfo = createTimelineInfoHtml({ ...job, job_id: jobId });
    const submittedTime = formatSubmittedTime(job);
    const totalDuration = getTotalJobDuration(job);
    const multiStageProgressBar = createMultiStageProgressBar(job, jobId);
    
    return `
        <div class="job" data-job-id="${jobId}">
            <div class="job-row">
                <div class="job-main-info">
                    <div class="job-header">
                        <div class="job-title-section">
                            <div class="job-header-line">
                                <button onclick="copyTrackInfo('${escapeHtml(trackInfo)}')" class="copy-track-btn" title="Copy track info to clipboard">
                                    📋
                                </button>
                                <span class="track-name">${trackInfo}</span>
                                <div class="job-status">
                                    <span class="status-badge status-${status}">${formatStatus(status)}</span>
                                </div>
                            </div>
                            <div class="job-id-info">
                                <span class="job-id">Job ${jobId}</span>
                            </div>
                        </div>
                        <div class="job-timing-section">
                            <div class="job-submitted">
                                <span class="timing-label">Submitted:</span>
                                <span class="timing-value">${submittedTime}</span>
                            </div>
                            <div class="job-duration">
                                <span class="timing-label">Duration:</span>
                                <span class="timing-value">${totalDuration}</span>
                            </div>
                        </div>
                    </div>
                    
                    ${multiStageProgressBar}
                    ${timelineInfo}
                </div>
                
                <div class="job-actions">
                    ${createJobActions(jobId, job)}
                </div>
            </div>
        </div>
    `;
}

function formatSubmittedTime(job) {
    // Helper function to format a timestamp in user's local timezone
    const formatLocalTime = (timestamp) => {
        try {
            const submitTime = parseServerTime(timestamp);
            
            // Validate the parsed time
            if (isNaN(submitTime.getTime())) {
                console.warn('Invalid submit time:', timestamp);
                return 'Invalid Time';
            }
            
            const now = new Date();
            const diffHours = (now - submitTime) / (1000 * 60 * 60);
            
            // Format options for consistent local timezone display
            const timeOptions = {
                hour: '2-digit', 
                minute: '2-digit',
                hour12: false  // Use 24-hour format for consistency
            };
            
            const dateOptions = {
                month: 'short', 
                day: 'numeric'
            };
            
            if (diffHours < 24 && diffHours >= 0) {
                // Same day - show just time
                return submitTime.toLocaleTimeString([], timeOptions);
            } else {
                // Different day - show date and time
                return submitTime.toLocaleDateString([], dateOptions) + ' ' + 
                       submitTime.toLocaleTimeString([], timeOptions);
            }
        } catch (error) {
            console.error('Error formatting submit time:', timestamp, error);
            return 'Error';
        }
    };
    
    // Try timeline data first (most accurate submission time)
    if (job.timeline && job.timeline.length > 0 && job.timeline[0].started_at) {
        return formatLocalTime(job.timeline[0].started_at);
    }
    
    // Fallback to created_at
    if (job.created_at) {
        return formatLocalTime(job.created_at);
    }
    
    return 'Unknown';
}

function getJobEndTime(job) {
    // Helper function to determine the correct end time for a job
    const status = job.status || 'unknown';
    const finishedStates = ['complete', 'error', 'cancelled', 'failed'];
    
    // If job is not finished, use current time
    if (!finishedStates.includes(status)) {
        return new Date();
    }
    
    // For finished jobs, try to find the actual completion time
    
    // 1. Check timeline data for the last phase end time
    if (job.timeline && job.timeline.length > 0) {
        // Find the last phase with an end time
        for (let i = job.timeline.length - 1; i >= 0; i--) {
            if (job.timeline[i].ended_at) {
                return parseServerTime(job.timeline[i].ended_at);
            }
        }
    }
    
    // 2. Check for job completion timestamp (if available)
    if (job.completed_at) {
        return parseServerTime(job.completed_at);
    }
    
    // 3. Check for job finished timestamp (if available)
    if (job.finished_at) {
        return parseServerTime(job.finished_at);
    }
    
    // 4. Check for job updated timestamp (if available and status is finished)
    if (job.updated_at && finishedStates.includes(status)) {
        return parseServerTime(job.updated_at);
    }
    
    // 5. Fallback to current time (shouldn't happen for finished jobs, but safety net)
    return new Date();
}

function getTotalJobDuration(job) {
    // Define phases where server-side processing is actually happening
    const processingPhases = ['queued', 'processing', 'rendering', 'finalizing'];
    
    // Try timeline summary first - sum only processing phases
    const timeline_summary = job.timeline_summary;
    if (timeline_summary && timeline_summary.phase_durations) {
        let processingDurationSeconds = 0;
        
        processingPhases.forEach(phase => {
            const phaseDuration = timeline_summary.phase_durations[phase];
            if (phaseDuration && phaseDuration > 0) {
                processingDurationSeconds += phaseDuration;
            }
        });
        
        if (processingDurationSeconds > 0) {
            return formatDuration(processingDurationSeconds);
        }
    }
    
    // Try calculating from timeline data directly - sum only processing phases
    if (job.timeline && job.timeline.length > 0) {
        try {
            let processingDurationSeconds = 0;
            
            job.timeline.forEach(timelineEntry => {
                if (processingPhases.includes(timelineEntry.status) && timelineEntry.duration_seconds) {
                    processingDurationSeconds += timelineEntry.duration_seconds;
                }
            });
            
            if (processingDurationSeconds > 0) {
                return formatDuration(processingDurationSeconds);
            }
            
            // Fallback: calculate total duration but note it may include waiting time
            const startTime = parseServerTime(job.timeline[0].started_at);
            const endTime = getJobEndTime(job);
            const durationMs = endTime - startTime;
            
            // Debug logging for timezone issues
            if (durationMs < 0) {
                console.warn('Negative duration detected:', {
                    startTime: startTime.toISOString(),
                    endTime: endTime.toISOString(),
                    originalTimestamp: job.timeline[0].started_at,
                    durationMs,
                    userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    jobStatus: job.status
                });
            }
            
            const durationSeconds = Math.floor(Math.max(0, durationMs) / 1000);
            return formatDuration(durationSeconds) + '*'; // Add asterisk to indicate it includes waiting time
        } catch (error) {
            console.error('Error calculating timeline duration:', error, job.timeline[0]);
        }
    }
    
    // Fallback to calculating from created_at if no timeline data
    // Note: This will include waiting time
    if (job.created_at) {
        try {
            const startTime = parseServerTime(job.created_at);
            const endTime = getJobEndTime(job);
            const durationMs = endTime - startTime;
            
            // Debug logging for timezone issues
            if (durationMs < 0) {
                console.warn('Negative duration detected from created_at:', {
                    startTime: startTime.toISOString(),
                    endTime: endTime.toISOString(),
                    originalTimestamp: job.created_at,
                    durationMs,
                    userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    jobStatus: job.status
                });
            }
            
            const durationSeconds = Math.floor(Math.max(0, durationMs) / 1000);
            return formatDuration(durationSeconds) + '*'; // Add asterisk to indicate it includes waiting time
        } catch (error) {
            console.error('Error calculating created_at duration:', error, job.created_at);
        }
    }
    
    return 'Unknown';
}

function createMultiStageProgressBar(job, jobId) {
    const timeline_summary = job.timeline_summary;
    const currentStatus = job.status || 'unknown';
    const timeline = job.timeline || [];
        
    // Define all possible phases in order with cleaner labels
    const allPhases = [
        { key: 'queued', label: 'Queued', shortLabel: 'Queue' },
        { key: 'processing', label: 'Processing', shortLabel: 'Process' },
        { key: 'awaiting_review', label: 'Review', shortLabel: 'Review' },
        { key: 'reviewing', label: 'Reviewing', shortLabel: 'Review' },
        { key: 'ready_for_finalization', label: 'Ready for Finalization', shortLabel: 'Ready' },
        { key: 'rendering', label: 'Rendering', shortLabel: 'Render' },
        { key: 'finalizing', label: 'Finalizing', shortLabel: 'Final' },
        { key: 'complete', label: 'Complete', shortLabel: 'Done' }
    ];
    
    // Define phases where server-side processing is actually happening
    const processingPhases = ['queued', 'processing', 'rendering', 'finalizing'];
    
    let html = '<div class="job-progress-enhanced">';
    html += `<div class="multi-stage-progress-bar clickable-progress-bar" onclick="console.log('🔍 Progress bar clicked for job:', '${jobId}'); showTimelineModal('${jobId}')" title="Click to view detailed timeline">`;
    
    if (timeline_summary && timeline_summary.phase_durations) {
        const phaseDurations = timeline_summary.phase_durations;
        
        // Calculate processing-only duration for width calculations
        let processingDurationSeconds = 0;
        processingPhases.forEach(phase => {
            const phaseDuration = phaseDurations[phase];
            if (phaseDuration && phaseDuration > 0) {
                processingDurationSeconds += phaseDuration;
            }
        });
        
        const totalDuration = processingDurationSeconds || 1;
        
        // Create segments for each phase that has occurred or is occurring
        let accumulatedWidth = 0;
        
        allPhases.forEach((phase) => {
            const duration = phaseDurations[phase.key];
            if (duration !== undefined && duration > 0) {
                // For processing phases, use proportional width based on processing duration
                // For non-processing phases, use fixed small width
                let widthPercent;
                if (processingPhases.includes(phase.key)) {
                    widthPercent = Math.max((duration / totalDuration) * 100, 8); // Minimum 8% width
                } else {
                    widthPercent = 5; // Fixed small width for non-processing phases (waiting phases)
                }
                
                const isActive = currentStatus === phase.key;
                const isCompleted = timeline.find(t => t.status === phase.key && t.ended_at);
                
                html += `
                    <div class="progress-segment ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''}" 
                         style="width: ${widthPercent}%; background-color: ${getPhaseColor(phase.key)}"
                         title="${phase.label}: ${formatDuration(duration)}">
                        <div class="segment-content">
                            <span class="segment-label">${phase.shortLabel}</span>
                            <span class="segment-duration">${formatDuration(duration)}</span>
                        </div>
                    </div>
                `;
                accumulatedWidth += widthPercent;
            }
        });
        
        // Add upcoming phases as placeholder segments
        const remainingPhases = allPhases.filter(phase => 
            !phaseDurations[phase.key] && 
            shouldShowPhase(phase.key, currentStatus)
        );
        
        if (remainingPhases.length > 0) {
            const remainingWidth = Math.max(100 - accumulatedWidth, 10);
            const segmentWidth = remainingWidth / remainingPhases.length;
            
            remainingPhases.forEach((phase) => {
                const isNext = isNextPhase(phase.key, currentStatus);
                
                html += `
                    <div class="progress-segment upcoming ${isNext ? 'next' : ''}" 
                         style="width: ${segmentWidth}%; background-color: ${getPhaseColor(phase.key, true)}"
                         title="${phase.label}: Pending">
                        <div class="segment-content">
                            <span class="segment-label">${phase.shortLabel}</span>
                            <span class="segment-duration">Pending</span>
                        </div>
                    </div>
                `;
            });
        }
    } else {
        // Fallback: simple progress based on status with elegant segments
        const progressPercent = job.progress || 0;
        const currentPhase = allPhases.find(p => p.key === currentStatus) || allPhases[0];
        
        // Show current phase
        html += `
            <div class="progress-segment active" 
                 style="width: ${Math.max(progressPercent, 15)}%; background-color: ${getPhaseColor(currentStatus)}"
                 title="${currentPhase.label}: ${progressPercent}%">
                <div class="segment-content">
                    <span class="segment-label">${currentPhase.shortLabel}</span>
                    <span class="segment-duration">${progressPercent}%</span>
                </div>
            </div>
        `;
        
        // Show remaining progress
        if (progressPercent < 100) {
            html += `
                <div class="progress-segment upcoming" 
                     style="width: ${100 - progressPercent}%; background-color: #e9ecef"
                     title="Remaining: ${100 - progressPercent}%">
                    <div class="segment-content">
                        <span class="segment-label">Remaining</span>
                        <span class="segment-duration">${100 - progressPercent}%</span>
                    </div>
                </div>
            `;
        }
    }
    
    html += '</div>';
    html += '</div>';
    
    return html;
}

function shouldShowPhase(phaseKey, currentStatus) {
    const phaseOrder = ['queued', 'processing', 'awaiting_review', 'reviewing', 'ready_for_finalization', 'rendering', 'finalizing', 'complete'];
    const currentIndex = phaseOrder.indexOf(currentStatus);
    const phaseIndex = phaseOrder.indexOf(phaseKey);
    
    // Show phases that come after the current status (except 'complete' which we handle specially)
    return phaseIndex > currentIndex && phaseKey !== 'complete';
}

function isNextPhase(phaseKey, currentStatus) {
    const phaseOrder = ['queued', 'processing', 'awaiting_review', 'reviewing', 'ready_for_finalization', 'rendering', 'finalizing', 'complete'];
    const currentIndex = phaseOrder.indexOf(currentStatus);
    const phaseIndex = phaseOrder.indexOf(phaseKey);
    
    return phaseIndex === currentIndex + 1;
}

function getPhaseColor(phase, isUpcoming = false) {
    const colors = {
        'queued': isUpcoming ? '#adb5bd' : '#6c757d',
        'processing': isUpcoming ? '#66a3ff' : '#007bff', 
        'awaiting_review': isUpcoming ? '#ffdf88' : '#ffc107',
        'reviewing': isUpcoming ? '#ff9f5c' : '#fd7e14',
        'ready_for_finalization': isUpcoming ? '#a0c4ff' : '#6c5ce7',
        'rendering': isUpcoming ? '#66d9a3' : '#28a745',
        'finalizing': isUpcoming ? '#74b9ff' : '#0984e3',
        'complete': isUpcoming ? '#66d9a3' : '#28a745',
        'error': isUpcoming ? '#ff8a9a' : '#dc3545',
        'timeout': isUpcoming ? '#ff9f66' : '#ff7f00'
    };
    return colors[phase] || (isUpcoming ? '#e9ecef' : '#6c757d');
}

function createTimelineInfoHtml(job) {
    const timeline_summary = job.timeline_summary;
    if (!timeline_summary || !timeline_summary.phase_durations) {
        return '';
    }
    
    const phases = timeline_summary.phase_durations;
    const totalDuration = timeline_summary.total_duration_formatted || '0s';
    
    // Create mini timeline visualization
    let timelineHtml = '<div class="job-timeline-mini">';
    timelineHtml += `<div class="timeline-total">Total: ${totalDuration}</div>`;
    
    if (Object.keys(phases).length > 0) {
        timelineHtml += '<div class="timeline-phases">';
        
        // Show up to 4 most significant phases
        const sortedPhases = Object.entries(phases)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 4);
        
        sortedPhases.forEach(([phase, duration]) => {
            const formattedDuration = formatDuration(duration);
            const phaseColor = getPhaseColor(phase);
            
            timelineHtml += `
                <span class="timeline-phase" style="background-color: ${phaseColor}" 
                      title="${formatStatus(phase)}: ${formattedDuration}">
                    ${getPhaseIcon(phase)} ${formattedDuration}
                </span>
            `;
        });
        
        timelineHtml += '</div>';
    }
    
    timelineHtml += '</div>';
    return timelineHtml;
}

function getPhaseIcon(phase) {
    const icons = {
        'queued': '⏳',
        'processing': '⚙️',
        'processing_audio': '🎵',
        'transcribing': '📝',
        'awaiting_review': '⏸️',
        'reviewing': '👁️',
        'ready_for_finalization': '🎵',
        'rendering': '🎬',
        'finalizing': '📦',
        'complete': '✅',
        'error': '❌'
    };
    return icons[phase] || '📋';
}

function getShortPhaseLabel(phase) {
    const shortLabels = {
        'queued': 'Queue',
        'processing': 'Process',
        'processing_audio': 'Audio',
        'transcribing': 'Lyrics',
        'awaiting_review': 'Review',
        'reviewing': 'Editing',
        'ready_for_finalization': 'Ready',
        'rendering': 'Render',
        'finalizing': 'Final',
        'complete': 'Done',
        'error': 'Error'
    };
    return shortLabels[phase] || phase;
}

// Timeline Modal Functions
async function showTimelineModal(jobId) {
    console.log('🔍 showTimelineModal called with jobId:', jobId);
    
    try {
        showInfo('Loading timeline data...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}/timeline`);
        
        if (!response.ok) {
            // If timeline endpoint fails, try to get basic job data and create a simple timeline
            console.warn('Timeline endpoint failed, trying basic job data...');
            const jobResponse = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}`);
            
            if (jobResponse && jobResponse.ok) {
                const jobData = await jobResponse.json();
                createSimpleTimelineModal(jobData);
                return;
            }
            
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const timelineData = await response.json();
        
        // Create and show the timeline modal
        createTimelineModal(timelineData);
        
    } catch (error) {
        console.error('Error loading timeline:', error);
        showError(`Error loading timeline: ${error.message}`);
    }
}

function createSimpleTimelineModal(jobData) {
    const modalHtml = `
        <div id="timeline-modal" class="modal">
            <div class="modal-content timeline-modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">⏱️ Timeline for ${jobData.artist || 'Unknown'} - ${jobData.title || 'Unknown'}</h3>
                    <div class="modal-controls">
                        <button onclick="closeTimelineModal()" class="modal-close">✕</button>
                    </div>
                </div>
                <div class="modal-body">
                    <div class="timeline-summary">
                        <div class="timeline-summary-cards">
                            <div class="timeline-card">
                                <div class="timeline-card-value">${getTotalJobDuration(jobData)}</div>
                                <div class="timeline-card-label">Total Time</div>
                            </div>
                            <div class="timeline-card">
                                <div class="timeline-card-value">${formatStatus(jobData.status)}</div>
                                <div class="timeline-card-label">Current Status</div>
                            </div>
                            <div class="timeline-card">
                                <div class="timeline-card-value">${jobData.progress || 0}%</div>
                                <div class="timeline-card-label">Progress</div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="simple-timeline-info">
                        <h4>Job Information</h4>
                        <p><strong>Status:</strong> ${formatStatus(jobData.status)}</p>
                        <p><strong>Progress:</strong> ${jobData.progress || 0}%</p>
                        <p><strong>Submitted:</strong> ${formatSubmittedTime(jobData)}</p>
                        <p><strong>Duration:</strong> ${getTotalJobDuration(jobData)}</p>
                        ${jobData.created_at ? `<p><strong>Created:</strong> ${formatTimestamp(jobData.created_at)}</p>` : ''}
                        <br>
                        <p><em>This job was created before detailed timeline tracking was implemented.</em></p>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Remove any existing timeline modal
    const existingModal = document.getElementById('timeline-modal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Show modal
    const modal = document.getElementById('timeline-modal');
    modal.style.display = 'flex';
    
    // Add click outside to close
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeTimelineModal();
        }
    });
}

function createTimelineModal(timelineData) {
    const modalHtml = `
        <div id="timeline-modal" class="modal">
            <div class="modal-content timeline-modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">⏱️ Timeline for ${timelineData.artist} - ${timelineData.title}</h3>
                    <div class="modal-controls">
                        <button onclick="closeTimelineModal()" class="modal-close">✕</button>
                    </div>
                </div>
                <div class="modal-body">
                    ${createTimelineVisualizationHtml(timelineData)}
                </div>
            </div>
        </div>
    `;
    
    // Remove any existing timeline modal
    const existingModal = document.getElementById('timeline-modal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Show modal
    const modal = document.getElementById('timeline-modal');
    modal.style.display = 'flex';
    
    // Add click outside to close
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeTimelineModal();
        }
    });
}

function calculateCorrectTimelineDuration(timelineData) {
    // Use the same corrected duration logic as the main job list
    const timeline = timelineData.timeline || [];
    
    if (timeline.length === 0) {
        return '0s';
    }
    
    try {
        const startTime = parseServerTime(timeline[0].started_at);
        const endTime = getJobEndTime({
            status: timelineData.current_status,
            timeline: timeline,
            completed_at: timelineData.completed_at,
            finished_at: timelineData.finished_at,
            updated_at: timelineData.updated_at
        });
        
        const durationMs = endTime - startTime;
        const durationSeconds = Math.floor(Math.max(0, durationMs) / 1000);
        return formatDuration(durationSeconds);
    } catch (error) {
        console.error('Error calculating timeline duration:', error);
        return '0s';
    }
}

function createTimelineVisualizationHtml(timelineData) {
    const timeline = timelineData.timeline || [];
    const summary = timelineData.timeline_summary || {};
    const metrics = timelineData.performance_metrics || {};
    
    let html = '';
    
    // Calculate corrected total duration using the same logic as job list
    const correctedTotalDuration = calculateCorrectTimelineDuration(timelineData);
    
    // Summary cards
    html += `
        <div class="timeline-summary">
            <div class="timeline-summary-cards">
                <div class="timeline-card">
                    <div class="timeline-card-value">${correctedTotalDuration}</div>
                    <div class="timeline-card-label">Total time</div>
                </div>
                <div class="timeline-card">
                    <div class="timeline-card-value">${timeline.length}</div>
                    <div class="timeline-card-label">Phases complete</div>
                </div>
                <div class="timeline-card">
                    <div class="timeline-card-value">${formatStatus(timelineData.current_status)}</div>
                    <div class="timeline-card-label">Current status</div>
                </div>
            </div>
        </div>
    `;
    
    // Visual timeline
    if (timeline.length > 0) {
        html += '<div class="timeline-visualization">';
        html += '<h4>Phase Timeline</h4>';
        html += '<div class="timeline-chart">';
        
        // Calculate all phase data first, but filter out "complete" status phases for the timeline bar
        const allPhaseData = timeline.map((phase, index) => {
            const startTime = parseServerTime(phase.started_at);
            
            // Check if this is the last phase of a finished job
            const isLastPhase = index === timeline.length - 1;
            const jobFinished = ['complete', 'error', 'cancelled', 'failed'].includes(timelineData.current_status);
            const shouldInferEndTime = isLastPhase && jobFinished && !phase.ended_at;
            
            let duration, endTime, isActive;
            
            if (phase.ended_at) {
                // Phase has explicit end time
                duration = phase.duration_seconds;
                endTime = parseServerTime(phase.ended_at);
                isActive = false;
            } else if (shouldInferEndTime) {
                // Infer end time for last phase of finished job
                endTime = getJobEndTime({
                    status: timelineData.current_status,
                    timeline: timeline,
                    completed_at: timelineData.completed_at,
                    finished_at: timelineData.finished_at,
                    updated_at: timelineData.updated_at
                });
                
                // Calculate duration if not provided
                if (phase.duration_seconds) {
                    duration = phase.duration_seconds;
                } else {
                    duration = Math.floor((endTime - startTime) / 1000);
                }
                isActive = false;
            } else {
                // Phase is genuinely in progress
                endTime = new Date();
                duration = phase.duration_seconds || Math.floor((new Date() - startTime) / 1000);
                isActive = true;
            }
            
            return { phase, duration, isActive };
        });
        
        // Filter out "complete" phases for the timeline bar (they're just end states)
        const phaseData = allPhaseData.filter(({ phase }) => phase.status !== 'complete');
        
        // Calculate proper widths based on actual durations
        const totalDuration = phaseData.reduce((total, { duration }) => total + (duration || 0), 0);
        
        if (totalDuration === 0) {
            // If all phases have 0 duration, give them equal small widths
            var normalizedWidths = new Array(phaseData.length).fill(100 / phaseData.length);
        } else {
            // Calculate proportional widths, but give 0-duration phases a tiny fixed width
            const meaningfulDuration = phaseData.reduce((total, { duration }) => total + (duration >= 1 ? duration : 0), 0);
            const tinyPhases = phaseData.filter(({ duration }) => !duration || duration < 1);
            const tinyPhaseWidth = 0.5; // 0.5% each for tiny phases
            const availableWidth = 100 - (tinyPhases.length * tinyPhaseWidth);
            
            var normalizedWidths = phaseData.map(({ duration }) => {
                if (!duration || duration < 1) {
                    return tinyPhaseWidth; // Tiny width for 0-duration phases
                }
                return meaningfulDuration > 0 ? (duration / meaningfulDuration) * availableWidth : availableWidth / phaseData.length;
            });
        }
        
        // Generate HTML for each phase
        phaseData.forEach(({ phase, duration, isActive }, index) => {
            const widthPercent = normalizedWidths[index] || 0.5;
            
            html += `
                <div class="timeline-phase-bar ${isActive ? 'active' : ''}" 
                     style="width: ${widthPercent}%; background-color: ${getPhaseColor(phase.status)}">
                    <div class="timeline-phase-info">
                        <div class="timeline-phase-name">
                            ${getPhaseIcon(phase.status)} ${getShortPhaseLabel(phase.status)}
                        </div>
                        <div class="timeline-phase-duration">
                            ${duration !== undefined && duration !== null ? formatDuration(duration) : (isActive ? 'Active' : 'Unknown')}
                        </div>
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
        html += '</div>';
        
        // Detailed phase table
        html += '<div class="timeline-details">';
        html += '<h4>Phase Details</h4>';
        html += '<div class="timeline-table">';
        html += `
            <div class="timeline-table-header">
                <div>Phase</div>
                <div>Started</div>
                <div>Ended</div>
                <div>Duration</div>
            </div>
        `;
        
        timeline.forEach((phase, index) => {
            const startTime = formatDetailedTimestamp(phase.started_at);
            
            // Check if this is the last phase of a finished job
            const isLastPhase = index === timeline.length - 1;
            const jobFinished = ['complete', 'error', 'cancelled', 'failed'].includes(timelineData.current_status);
            const shouldInferEndTime = isLastPhase && jobFinished && !phase.ended_at;
            
            let endTime, duration, isActive;
            
            if (phase.ended_at) {
                // Phase has explicit end time
                endTime = formatDetailedTimestamp(phase.ended_at);
                duration = phase.duration_seconds ? formatDuration(phase.duration_seconds) : 'Unknown';
                isActive = false;
            } else if (shouldInferEndTime) {
                // Infer end time for last phase of finished job
                const jobEndTime = getJobEndTime({
                    status: timelineData.current_status,
                    timeline: timeline,
                    completed_at: timelineData.completed_at,
                    finished_at: timelineData.finished_at,
                    updated_at: timelineData.updated_at
                });
                endTime = formatDetailedTimestamp(jobEndTime.toISOString());
                
                // Calculate duration if not provided
                if (phase.duration_seconds) {
                    duration = formatDuration(phase.duration_seconds);
                } else {
                    const startDate = parseServerTime(phase.started_at);
                    const durationSeconds = Math.floor((jobEndTime - startDate) / 1000);
                    duration = formatDuration(Math.max(0, durationSeconds));
                }
                isActive = false;
            } else {
                // Phase is genuinely in progress
                endTime = 'In Progress';
                duration = 'In Progress';
                isActive = true;
            }
            
            html += `
                <div class="timeline-table-row ${isActive ? 'active' : ''}">
                    <div class="timeline-phase-cell">
                        ${getPhaseIcon(phase.status)} ${formatStatus(phase.status)}
                    </div>
                    <div>${startTime}</div>
                    <div>${endTime}</div>
                    <div>${duration}</div>
                </div>
            `;
        });
        
        html += '</div>';
        html += '</div>';
    }
    
    return html;
}

function formatDetailedTimestamp(isoString) {
    try {
        const date = parseServerTime(isoString);
        
        // Validate the parsed date
        if (isNaN(date.getTime())) {
            console.warn('Invalid detailed timestamp:', isoString);
            return 'Invalid Time';
        }
        
        // Format with consistent local timezone display
        return date.toLocaleString([], {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false  // Use 24-hour format for consistency
        });
    } catch (error) {
        console.error('Error formatting detailed timestamp:', isoString, error);
        return 'Error';
    }
}

function formatDuration(seconds) {
    if (seconds < 60) {
        return `${seconds}s`;
    } else if (seconds < 3600) {
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        return `${minutes}m ${remainingSeconds}s`;
    } else {
        const hours = Math.floor(seconds / 3600);
        const remainingMinutes = Math.floor((seconds % 3600) / 60);
        return `${hours}h ${remainingMinutes}m`;
    }
}

function closeTimelineModal() {
    const modal = document.getElementById('timeline-modal');
    if (modal) {
        modal.remove();
    }
}

function createJobActions(jobId, job) {
    const status = job.status || 'unknown';
    const actions = [];
    
    // Status-specific actions
    if (status === 'awaiting_review') {
        if (job.review_url) {
            actions.push(`<a href="${job.review_url}" target="_blank" class="btn btn-success">📝 Review Lyrics</a>`);
        } else {
            actions.push(`<button onclick="reviewLyrics('${jobId}')" class="btn btn-success">📝 Review Lyrics</button>`);
        }
    }
    
    if (status === 'reviewing') {
        // For reviewing status, show option to complete review (no instrumental selection yet)
        actions.push(`<button onclick="completeReview('${jobId}')" class="btn btn-success">✅ Complete Review</button>`);
        // Also allow continuing the review
        const reviewUrl = `https://lyrics.nomadkaraoke.com/?baseApiUrl=${API_BASE_URL}/corrections/${jobId}`;
        actions.push(`<a href="${reviewUrl}" target="_blank" class="btn btn-secondary">📝 Continue Review</a>`);
    }
    
    if (status === 'ready_for_finalization') {
        // For ready_for_finalization status, show option to choose instrumental and finalize
        actions.push(`<button onclick="showInstrumentalSelectionForJob('${jobId}')" class="btn btn-success">🎵 Choose Instrumental</button>`);
    }
    
    // Add YouTube link button if available
    if (job.youtube_url) {
        actions.push(`<a href="${job.youtube_url}" target="_blank" class="btn btn-success">🎥 Watch on YouTube</a>`);
    }

    if (status === 'complete') {
        actions.push(`<button onclick="downloadVideo('${jobId}')" class="btn btn-success">📥 Download MP4 Video</button>`);
        actions.push(`<button onclick="showFilesModal('${jobId}')" class="btn btn-info">📁 View All Output Files</button>`);
        actions.push(`<button onclick="copyDeliveryMessage('${jobId}')" class="btn btn-secondary">📧 Copy Delivery Message</button>`);
    }
    
    // Add Dropbox sharing link button if available
    if (job.brand_code_dir_sharing_link) {
        actions.push(`<a href="${job.brand_code_dir_sharing_link}" target="_blank" class="btn btn-info">📁 Open Dropbox Folder</a>`);
    }
    
    if (status === 'error') {
        actions.push(`<button onclick="retryJob('${jobId}')" class="btn btn-warning">🔄 Retry</button>`);
    }
    
    if (status === 'timeout') {
        actions.push(`<button onclick="retryJob('${jobId}')" class="btn btn-warning">🔄 Retry</button>`);
    }
    
    // Admin-only clone action
    if (currentUser && currentUser.admin_access) {
        // Show clone button for jobs that have completed at least phase 1, or failed jobs
        const completableStatuses = ['awaiting_review', 'reviewing', 'ready_for_finalization', 'rendering', 'finalizing', 'complete', 'error', 'timeout'];
        if (completableStatuses.includes(status)) {
            actions.push(`<button onclick="showCloneJobModal('${jobId}')" class="btn btn-info">🔄 Clone Job</button>`);
        }
    }

    // Always available actions
    actions.push(`<button onclick="tailJobLogs('${jobId}')" class="btn btn-info">📜 View Logs</button>`);
    
    // Admin-only status override action
    if (currentUser && currentUser.admin_access) {
        actions.push(`<button onclick="showManualStatusUpdate('${jobId}')" class="btn btn-warning">⚙️ Status Override</button>`);
    }
    
    actions.push(`<button onclick="deleteJob('${jobId}')" class="btn btn-danger">🗑️ Delete</button>`);
    
    return actions.join(' ');
}

function tailJobLogs(jobId) {
    // Stop any existing tail
    stopLogTail();
    
    // Show modal for log tailing
    const modalShown = showLogTailModal(jobId);
    if (!modalShown) {
        console.error('Failed to show modal for job:', jobId);
        return;
    }
    
    currentTailJobId = jobId;
    
    // Start tailing
    logTailInterval = setInterval(() => {
        loadLogTailData(jobId);
    }, 2000); // Update every 2 seconds
    
    // Load initial data
    loadLogTailData(jobId);
}

function stopLogTail() {
    if (logTailInterval) {
        clearInterval(logTailInterval);
        logTailInterval = null;
    }
    currentTailJobId = null;
}

function showLogTailModal(jobId) {
    const modal = document.getElementById('log-tail-modal');
    const modalJobId = document.getElementById('modal-job-id');
    const modalLogs = document.getElementById('modal-logs');
    
    if (modal && modalJobId && modalLogs) {
        // Force reset any previous state completely
        modal.style.display = 'none';
        
        // Clear any existing intervals or state
        if (logTailInterval) {
            clearInterval(logTailInterval);
            logTailInterval = null;
        }
        if (logRefreshInterval) {
            clearInterval(logRefreshInterval);
            logRefreshInterval = null;
        }
        currentTailJobId = null;
        
        // Store job ID on modal for server-side filtering
        modal.setAttribute('data-job-id', jobId);
        
        // Set up modal content
        modalJobId.textContent = jobId;
        modalLogs.innerHTML = '<div class="logs-loading">Starting log tail...</div>';
        
        // Apply current font size
        updateLogsFontSize();
        
        // Reset auto-scroll state
        autoScrollEnabled = false;
        const autoScrollBtn = document.getElementById('auto-scroll-btn');
        if (autoScrollBtn) {
            autoScrollBtn.classList.remove('toggle-active');
            autoScrollBtn.textContent = '⏸️ Manual';
            autoScrollBtn.title = 'Auto-scroll disabled - click to enable';
        }
        
        // Reset filter state and clear previous data
        logFilters = {
            include: '',
            exclude: '',
            level: '',
            limit: 1000,
            regex: false
        };
        currentLogData = null;
        
        // Reset filter input values
        const includeFilter = document.getElementById('log-include-filter');
        const excludeFilter = document.getElementById('log-exclude-filter');
        const levelFilter = document.getElementById('log-level-filter');
        const limitInput = document.getElementById('log-limit-input');
        
        if (includeFilter) includeFilter.value = '';
        if (excludeFilter) excludeFilter.value = '';
        if (levelFilter) levelFilter.value = '';
        if (limitInput) limitInput.value = '1000';
        
        // Clear regex toggle
        const regexBtn = document.getElementById('regex-mode-btn');
        if (regexBtn) {
            regexBtn.classList.remove('active');
            regexBtn.style.fontWeight = 'normal';
            regexBtn.title = 'Toggle regex mode';
        }
        
        // Reset filter stats
        const statsElement = document.getElementById('filter-stats');
        if (statsElement) {
            statsElement.textContent = 'Loading...';
        }
        
        // Reset auto-refresh button state
        logAutoRefreshEnabled = true;
        const logAutoRefreshBtn = document.getElementById('log-auto-refresh-btn');
        if (logAutoRefreshBtn) {
            logAutoRefreshBtn.classList.add('toggle-active');
            logAutoRefreshBtn.textContent = '🔄 Refresh';
            logAutoRefreshBtn.title = 'Auto-refresh enabled - click to disable';
        }
        
        // Force reflow and show modal
        modal.offsetHeight; // Trigger reflow
        modal.style.display = 'flex';
        
        // Load initial logs with server-side filtering
        setTimeout(async () => {
            await fetchFilteredLogs();
            if (autoScrollEnabled) {
                scrollToBottom();
            }
        }, 100);

        return true;
    } else {
        console.error('Modal elements not found:', { modal: !!modal, modalJobId: !!modalJobId, modalLogs: !!modalLogs });
        return false;
    }
}

function closeLogTailModal() {
    const modal = document.getElementById('log-tail-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    
    // Stop any log tailing and refresh intervals
    stopLogTail();
    if (logRefreshInterval) {
        clearInterval(logRefreshInterval);
        logRefreshInterval = null;
    }
    
    // Reset auto-scroll to enabled for next time
    autoScrollEnabled = false;
    const autoScrollBtn = document.getElementById('auto-scroll-btn');
    if (autoScrollBtn) {
        autoScrollBtn.classList.remove('toggle-active');
        autoScrollBtn.textContent = '⏸️ Manual';
        autoScrollBtn.title = 'Auto-scroll disabled - click to enable';
    }
    
    // Reset filter state
    logFilters = {
        include: '',
        exclude: '',
        level: '',
        limit: 1000,
        regex: false
    };
    currentLogData = null;
    
    // Reset auto-refresh state
    logAutoRefreshEnabled = true;
    
    // Clear modal content to ensure fresh state
    const modalLogs = document.getElementById('modal-logs');
    if (modalLogs) {
        modalLogs.innerHTML = '';
    }
    
    const modalJobId = document.getElementById('modal-job-id');
    if (modalJobId) {
        modalJobId.textContent = '';
    }
    
    // Remove job ID from modal
    if (modal) {
        modal.removeAttribute('data-job-id');
    }
}

async function loadLogTailData(jobId) {
    const modal = document.getElementById('log-tail-modal');
    const modalLogs = document.getElementById('modal-logs');
    
    // Check if modal is still open before proceeding
    if (!modal || modal.style.display === 'none' || !modalLogs) {
        stopLogTail();
        return;
    }
    
    // Check if user has selected text - if so, skip this update to avoid interrupting copy/paste
    const selection = window.getSelection();
    const hasSelection = selection && selection.toString().length > 0;
    
    // Also check if the selection is within the modal logs area
    let selectionInLogs = false;
    if (hasSelection && selection.rangeCount > 0) {
        const range = selection.getRangeAt(0);
        selectionInLogs = modalLogs.contains(range.commonAncestorContainer) || 
                         modalLogs.contains(range.startContainer) || 
                         modalLogs.contains(range.endContainer);
    }
    
    if (selectionInLogs) {
        // User is selecting text in logs - skip content update but still update title
        try {
            const statusResponse = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}`);
            if (!statusResponse) return;
            const status = await statusResponse.json();
            
            const modalTitle = document.querySelector('#log-tail-modal .modal-title');
            if (modalTitle) {
                modalTitle.innerHTML = `Log Tail - Job <span id="modal-job-id">${jobId}</span> - ${formatStatus(status.status)} (${status.progress || 0}%) [Selection Active]`;
            }
        } catch (error) {
            console.error('Error loading status:', error);
        }
        return;
    }
    
    // Check if auto-refresh is disabled
    if (!logAutoRefreshEnabled) {
        // Still update the title but skip log fetching
        try {
            const statusResponse = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}`);
            if (!statusResponse) return;
            const status = await statusResponse.json();
            
            const modalTitle = document.querySelector('#log-tail-modal .modal-title');
            if (modalTitle) {
                modalTitle.innerHTML = `Log Tail - Job <span id="modal-job-id">${jobId}</span> - ${formatStatus(status.status)} (${status.progress || 0}%) [Paused]`;
            }
        } catch (error) {
            console.error('Error loading status:', error);
        }
        return;
    }
    
    try {
        // Only fetch job status for title update
        const statusResponse = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}`);
        
        if (!statusResponse) return; // Auth failed, already handled
        
        const status = await statusResponse.json();
        
        // Update modal title with current status - preserve structure
        const modalJobIdSpan = document.getElementById('modal-job-id');
        const modalTitle = document.querySelector('#log-tail-modal .modal-title');
        if (modalJobIdSpan && modalTitle) {
            // Keep the original structure but update the content after the job ID
            modalTitle.innerHTML = `Log Tail - Job <span id="modal-job-id">${jobId}</span> - ${formatStatus(status.status)} (${status.progress || 0}%)`;
        } else if (modalTitle) {
            // Fallback if span is missing - recreate the full structure
            modalTitle.innerHTML = `Log Tail - Job <span id="modal-job-id">${jobId}</span> - ${formatStatus(status.status)} (${status.progress || 0}%)`;
        }
        
        // Fetch filtered logs from server
        await fetchFilteredLogs();
        
        // Auto-scroll to bottom if auto-scroll is enabled
        if (autoScrollEnabled) {
            modalLogs.scrollTop = modalLogs.scrollHeight;
        }
        
    } catch (error) {
        console.error('Error loading tail data:', error);
        modalLogs.innerHTML = `<p class="error">Failed to load logs: ${error.message}</p>`;
    }
}

// Admin functions
async function refreshData() {
    await loadJobs();
    showSuccess('Data refreshed successfully');
}

async function clearErrorJobs() {
    if (!confirm('Are you sure you want to clear all error jobs?')) return;
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/clear-errors`, {
            method: 'POST'
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.status === 'success') {
            showSuccess(result.message);
            await loadJobs();
        } else {
            showError(result.message || 'Failed to clear error jobs');
        }
    } catch (error) {
        console.error('Error clearing error jobs:', error);
        showError('Failed to clear error jobs: ' + error.message);
    }
}

function exportLogs() {
    window.open(`${API_BASE_URL}/admin/export-logs`);
}

async function viewCacheStats() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cache/stats`);
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const stats = await response.json();
            showCacheStatsModal(stats);
        } else {
            const stats = await response.json();
            showError(stats.error || 'Failed to load cache stats');
        }
    } catch (error) {
        console.error('Error loading cache stats:', error);
        showError('Failed to load cache stats: ' + error.message);
    }
}

async function clearCache() {
    if (!confirm('Are you sure you want to clear old cache files (90+ days)? This cannot be undone.')) return;
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cache/clear`, {
            method: 'POST'
        });
        
        if (!response) return; // Auth failed, already handled
        const result = await response.json();
        
        if (result.status === 'success') {
            showSuccess(result.message);
            // Refresh cache stats if modal is open
            const modal = document.getElementById('cache-stats-modal');
            if (modal && modal.style.display !== 'none') {
                viewCacheStats();
            }
        } else {
            showError(result.message || 'Failed to clear cache');
        }
    } catch (error) {
        console.error('Error clearing cache:', error);
        showError('Failed to clear cache: ' + error.message);
    }
}

async function warmCache() {
    try {
        showInfo('Initiating cache warming...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cache/warm`, {
            method: 'POST'
        });
        
        if (!response) return; // Auth failed, already handled
        const result = await response.json();
        
        if (result.status === 'success') {
            showSuccess(result.message);
        } else {
            showError(result.message || 'Failed to warm cache');
        }
    } catch (error) {
        console.error('Error warming cache:', error);
        showError('Failed to warm cache: ' + error.message);
    }
}

// Log Level Management Functions (Admin Only)
async function showLogLevelSettings() {
    if (!currentUser || !currentUser.admin_access) {
        showError('Admin access required');
        return;
    }
    
    try {
        showInfo('Loading current log level...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/system/log-level`);
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const result = await response.json();
            displayLogLevelSettings(result);
        } else {
            const error = await response.json();
            showError('Error loading log level: ' + error.message);
        }
    } catch (error) {
        console.error('Error loading log level:', error);
        showError('Error loading log level: ' + error.message);
    }
}

function displayLogLevelSettings(logLevelData) {
    const currentLevel = logLevelData.log_level;
    const availableLevels = logLevelData.available_levels;
    
    // Create log level selection UI
    let levelOptions = '';
    availableLevels.forEach(level => {
        const selected = level === currentLevel ? 'selected' : '';
        const description = getLogLevelDescription(level);
        levelOptions += `<option value="${level}" ${selected}>${level} - ${description}</option>`;
    });
    
    const logLevelHtml = `
        <div class="log-level-settings">
            <h4>🔍 System Log Level</h4>
            <div class="log-level-info">
                <p><strong>Current Level:</strong> ${currentLevel}</p>
                <p class="log-level-description">${logLevelData.description}</p>
            </div>
            
            <div class="log-level-controls">
                <div class="form-group">
                    <label for="log-level-select">Change Log Level:</label>
                    <select id="log-level-select" class="form-control">
                        ${levelOptions}
                    </select>
                    <small class="help-text">
                        Changes will apply to new jobs only. Existing running jobs will keep their current log level.
                    </small>
                </div>
                
                <div class="log-level-actions">
                    <button onclick="applyLogLevel()" class="btn btn-primary">
                        💾 Apply Log Level
                    </button>
                    <button onclick="showLogLevelSettings()" class="btn btn-secondary">
                        🔄 Refresh
                    </button>
                </div>
            </div>
            
            <div class="log-level-help">
                <h5>Log Level Guide:</h5>
                <ul>
                    <li><strong>DEBUG:</strong> Very verbose output including internal processing details</li>
                    <li><strong>INFO:</strong> Standard operational messages (recommended for normal use)</li>
                    <li><strong>WARNING:</strong> Only warnings and errors</li>
                    <li><strong>ERROR:</strong> Only error messages</li>
                    <li><strong>CRITICAL:</strong> Only critical system errors</li>
                </ul>
                <p><em>Note: DEBUG level will produce significantly more log output and may impact performance.</em></p>
            </div>
        </div>
    `;
    
    // Display in a dedicated section or modal
    const adminPanel = document.getElementById('admin-panel');
    if (adminPanel) {
        // Look for existing log level section
        let logLevelSection = adminPanel.querySelector('.log-level-section');
        if (!logLevelSection) {
            // Create new section if it doesn't exist
            logLevelSection = document.createElement('div');
            logLevelSection.className = 'admin-section log-level-section';
            adminPanel.appendChild(logLevelSection);
        }
        logLevelSection.innerHTML = logLevelHtml;
                
        showSuccess('Log level settings loaded');
    }
}

function getLogLevelDescription(level) {
    const descriptions = {
        'DEBUG': 'Verbose debugging information',
        'INFO': 'Standard operational messages',
        'WARNING': 'Warnings and errors only',
        'ERROR': 'Error messages only',
        'CRITICAL': 'Critical system errors only'
    };
    return descriptions[level] || 'Unknown level';
}

async function applyLogLevel() {
    const selectElement = document.getElementById('log-level-select');
    if (!selectElement) {
        showError('Log level selector not found');
        return;
    }
    
    const newLogLevel = selectElement.value;
    if (!newLogLevel) {
        showError('Please select a log level');
        return;
    }
    
    try {
        showInfo(`Setting log level to ${newLogLevel}...`);
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/system/log-level`, {
            method: 'POST',
            body: JSON.stringify({ log_level: newLogLevel })
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(result.message);
            if (result.note) {
                setTimeout(() => showInfo(result.note), 2000);
            }
            // Refresh the display to show the new current level
            setTimeout(() => showLogLevelSettings(), 1000);
        } else {
            showError('Error setting log level: ' + result.message);
        }
    } catch (error) {
        console.error('Error setting log level:', error);
        showError('Error setting log level: ' + error.message);
    }
}

// Manual Status Update Functions (Admin Only)
async function showManualStatusUpdate(jobId = null) {
    if (!currentUser || !currentUser.admin_access) {
        showError('Admin access required');
        return;
    }

    const modal = document.getElementById('manual-status-update-modal');
    const form = document.getElementById('manual-status-update-form');
    const result = document.getElementById('status-update-result');
    
    // Reset form and result
    form.reset();
    result.style.display = 'none';
    
    // Pre-fill job ID if provided
    if (jobId) {
        const jobIdInput = document.getElementById('status-update-job-id');
        if (jobIdInput) {
            jobIdInput.value = jobId;
        }
    }
    
    // Show modal
    modal.style.display = 'block';
    
    // Set up form submission handler
    form.onsubmit = handleManualStatusUpdate;
}

async function handleManualStatusUpdate(event) {
    event.preventDefault();
    
    const submitBtn = event.target.querySelector('button[type="submit"]');
    const result = document.getElementById('status-update-result');
    
    // Get form values
    const jobId = document.getElementById('status-update-job-id').value.trim();
    const newStatus = document.getElementById('status-update-new-status').value;
    const progress = document.getElementById('status-update-progress').value;
    const additionalDataText = document.getElementById('status-update-additional-data').value.trim();
    
    // Validate required fields
    if (!jobId || !newStatus) {
        showError('Job ID and new status are required');
        return;
    }
    
    // Parse additional data if provided
    let additionalData = {};
    if (additionalDataText) {
        try {
            additionalData = JSON.parse(additionalDataText);
        } catch (error) {
            showError('Invalid JSON in additional data field');
            return;
        }
    }
    
    // Prepare request data
    const requestData = {
        new_status: newStatus,
        additional_data: additionalData
    };
    
    if (progress !== '') {
        requestData.progress = parseInt(progress);
    }
    
    try {
        // Show loading state
        submitBtn.disabled = true;
        submitBtn.textContent = '🔄 Updating...';
        result.style.display = 'none';
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/jobs/${jobId}/update-status`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        });
        
        if (!response) return; // Auth failed, already handled
        
        const responseData = await response.json();
        
        if (responseData.success) {
            // Show success message
            result.className = 'status-update-result success';
            result.innerHTML = `
                <h4>✅ Status Updated Successfully</h4>
                <p><strong>Job ID:</strong> ${jobId}</p>
                <p><strong>Old Status:</strong> ${responseData.old_status}</p>
                <p><strong>New Status:</strong> ${responseData.new_status}</p>
                <p><strong>Last Updated:</strong> ${responseData.updated_job_data.last_updated}</p>
                <p><strong>Timeline Entries:</strong> ${responseData.updated_job_data.timeline_entries}</p>
                <div class="success-note">
                    <strong>Note:</strong> The status has been updated with timeline tracking. 
                    An audit trail has been added to track this manual update.
                </div>
            `;
            result.style.display = 'block';
            
            // Show success notification
            showSuccess(`Job ${jobId} status updated to "${responseData.new_status}"`);
            
            // Refresh jobs list to show the updated status
            loadJobs();
            
        } else {
            // Show error message
            result.className = 'status-update-result error';
            result.innerHTML = `
                <h4>❌ Update Failed</h4>
                <p><strong>Error:</strong> ${responseData.message}</p>
            `;
            result.style.display = 'block';
            
            showError(`Failed to update job status: ${responseData.message}`);
        }
        
    } catch (error) {
        console.error('Error updating job status:', error);
        
        result.className = 'status-update-result error';
        result.innerHTML = `
            <h4>❌ Update Failed</h4>
            <p><strong>Error:</strong> ${error.message}</p>
            <p>Check the console for more details.</p>
        `;
        result.style.display = 'block';
        
        showError(`Failed to update job status: ${error.message}`);
        
    } finally {
        // Reset button state
        submitBtn.disabled = false;
        submitBtn.textContent = '🔄 Update Job Status';
    }
}

function closeManualStatusUpdateModal() {
    const modal = document.getElementById('manual-status-update-modal');
    modal.style.display = 'none';
}

function setStatusPreset(presetType) {
    const newStatusSelect = document.getElementById('status-update-new-status');
    const progressInput = document.getElementById('status-update-progress');
    const additionalDataTextarea = document.getElementById('status-update-additional-data');
    
    // Clear previous values
    newStatusSelect.value = '';
    progressInput.value = '';
    additionalDataTextarea.value = '';
    
    // Set values based on preset type
    switch (presetType) {
        case 'phase1_timeout':
            newStatusSelect.value = 'timeout';
            progressInput.value = '0';
            additionalDataTextarea.value = JSON.stringify({
                "error": "Function timed out after 600 seconds (10 minutes)",
                "timeout_duration_seconds": 600,
                "restart_available": true
            }, null, 2);
            break;
            
        case 'phase2_timeout':
            newStatusSelect.value = 'timeout';
            progressInput.value = '0';
            additionalDataTextarea.value = JSON.stringify({
                "error": "Function timed out after 600 seconds (10 minutes)",
                "timeout_duration_seconds": 600,
                "restart_available": true
            }, null, 2);
            break;
            
        case 'phase3_timeout':
            newStatusSelect.value = 'timeout';
            progressInput.value = '0';
            additionalDataTextarea.value = JSON.stringify({
                "error": "Function timed out after 600 seconds (10 minutes)",
                "timeout_duration_seconds": 600,
                "restart_available": true
            }, null, 2);
            break;
            
        case 'processing_error':
            newStatusSelect.value = 'error';
            progressInput.value = '0';
            additionalDataTextarea.value = JSON.stringify({
                "error": "Processing failed due to audio separation error",
                "traceback": "Traceback (most recent call last):\n  File \"core.py\", line 123, in process_audio\n    raise Exception(\"Audio separation failed\")\nException: Audio separation failed"
            }, null, 2);
            break;
            
        case 'force_complete':
            newStatusSelect.value = 'complete';
            progressInput.value = '100';
            additionalDataTextarea.value = JSON.stringify({
                "video_path": "/output/job_id/Final Video.mp4",
                "lrc_path": "/output/job_id/Karaoke.lrc",
                "final_files": {
                    "lossless_4k": "/output/job_id/Final Karaoke Lossless.mkv",
                    "lossy_4k": "/output/job_id/Final Karaoke.mp4",
                    "compressed_720p": "/output/job_id/Final Karaoke 720p.mp4"
                }
            }, null, 2);
            break;
            
        case 'reset_queued':
            newStatusSelect.value = 'queued';
            progressInput.value = '0';
            additionalDataTextarea.value = JSON.stringify({
                "created_at": new Date().toISOString(),
                "restart_from_beginning": true
            }, null, 2);
            break;
            
        case 'await_review':
            newStatusSelect.value = 'awaiting_review';
            progressInput.value = '75';
            additionalDataTextarea.value = JSON.stringify({
                "track_data": {
                    "artist": "Test Artist",
                    "title": "Test Title"
                },
                "track_output_dir": "/output/job_id",
                "corrections_file": "/output/job_id/lyrics/Test Artist - Test Title (Lyrics Corrections).json"
            }, null, 2);
            break;
            
        case 'ready_final':
            newStatusSelect.value = 'ready_for_finalization';
            progressInput.value = '85';
            additionalDataTextarea.value = JSON.stringify({
                "video_path": "/output/job_id/Test Artist - Test Title (With Vocals).mkv",
                "lrc_path": "/output/job_id/Test Artist - Test Title (Karaoke).lrc",
                "output_files_partial": {
                    "video": "/output/job_id/Test Artist - Test Title (With Vocals).mkv",
                    "lrc": "/output/job_id/Test Artist - Test Title (Karaoke).lrc"
                }
            }, null, 2);
            break;
    }
    
    // Add visual feedback
    const button = event?.target;
    if (button) {
        const originalText = button.innerHTML;
        button.innerHTML = '✓ Applied';
        button.style.opacity = '0.7';
        
        setTimeout(() => {
            button.innerHTML = originalText;
            button.style.opacity = '1';
        }, 1000);
    }
}

async function loadAudioShakeCache() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cache/audioshake`);
        
        if (!response) return []; // Auth failed, return empty array
        const result = await response.json();
        
        if (response.ok && result.status === 'success') {
            return result.cached_responses;
        } else {
            console.error('Failed to load AudioShake cache:', result);
            return [];
        }
    } catch (error) {
        console.error('Error loading AudioShake cache:', error);
        return [];
    }
}

async function deleteAudioShakeCache(audioHash) {
    if (!confirm(`Are you sure you want to delete the cached AudioShake response for hash ${audioHash}?`)) return;
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/cache/audioshake/${audioHash}`, {
            method: 'DELETE'
        });
        
        if (!response) return; // Auth failed, already handled
        const result = await response.json();
        
        if (result.status === 'success') {
            showSuccess(result.message);
            // Refresh cache stats if modal is open
            const modal = document.getElementById('cache-stats-modal');
            if (modal && modal.style.display !== 'none') {
                viewCacheStats();
            }
        } else {
            showError(result.message || 'Failed to delete cached response');
        }
    } catch (error) {
        console.error('Error deleting cached response:', error);
        showError('Failed to delete cached response: ' + error.message);
    }
}

function showCacheStatsModal(stats) {
    const modal = document.getElementById('cache-stats-modal');
    if (!modal) {
        console.error('Cache stats modal not found');
        return;
    }
    
    // Update cache stats content
    updateCacheStatsContent(stats);
    
    // Show modal
    modal.style.display = 'flex';
}

function closeCacheStatsModal() {
    const modal = document.getElementById('cache-stats-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

async function updateCacheStatsContent(stats) {
    const statsContainer = document.getElementById('cache-stats-content');
    if (!statsContainer) return;
    
    // Load AudioShake cache data
    const audioShakeCache = await loadAudioShakeCache();
    
    const formatBytes = (bytes) => {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    };
    
    const formatFileCount = (count) => {
        return count === 1 ? '1 file' : `${count} files`;
    };
    
    let html = `
        <div class="cache-overview">
            <h4>📊 Cache Overview</h4>
            <div class="cache-summary">
                <div class="cache-stat">
                    <span class="cache-stat-label">Total Files:</span>
                    <span class="cache-stat-value">${stats.total_files}</span>
                </div>
                <div class="cache-stat">
                    <span class="cache-stat-label">Total Size:</span>
                    <span class="cache-stat-value">${formatBytes(stats.total_size_bytes)} (${stats.total_size_gb} GB)</span>
                </div>
            </div>
        </div>
        
        <div class="cache-directories">
            <h4>📁 Cache Directories</h4>
            <div class="cache-dirs-grid">
    `;
    
    // Cache directory stats
    const dirLabels = {
        'audio_hashes': '🎵 Audio Hashes',
        'audioshake_responses': '🔊 AudioShake API',
        'models': '🤖 Model Files',
        'transcriptions': '📝 Transcriptions'
    };
    
    Object.entries(stats.cache_directories || {}).forEach(([dirName, dirStats]) => {
        const label = dirLabels[dirName] || dirName;
        html += `
            <div class="cache-dir-card">
                <div class="cache-dir-header">${label}</div>
                <div class="cache-dir-stats">
                    <div>${formatFileCount(dirStats.file_count)}</div>
                    <div>${formatBytes(dirStats.size_bytes)}</div>
                </div>
            </div>
        `;
    });
    
    html += `
            </div>
        </div>
    `;
    
    // AudioShake cache details
    if (audioShakeCache.length > 0) {
        html += `
            <div class="audioshake-cache">
                <h4>🔊 AudioShake Cache Details</h4>
                <div class="audioshake-cache-list">
        `;
        
        audioShakeCache.forEach(item => {
            const timestamp = formatTimestamp(item.timestamp);
            const shortHash = item.audio_hash.substring(0, 12) + '...';
            
            html += `
                <div class="audioshake-cache-item">
                    <div class="cache-item-info">
                        <div class="cache-item-hash">${shortHash}</div>
                        <div class="cache-item-timestamp">${timestamp}</div>
                        <div class="cache-item-size">${formatBytes(item.file_size_bytes)}</div>
                    </div>
                    <button onclick="deleteAudioShakeCache('${item.audio_hash}')" class="btn btn-danger btn-sm">
                        🗑️ Delete
                    </button>
                </div>
            `;
        });
        
        html += `
                </div>
            </div>
        `;
    } else {
        html += `
            <div class="audioshake-cache">
                <h4>🔊 AudioShake Cache Details</h4>
                <p class="no-cache">No AudioShake responses cached yet.</p>
            </div>
        `;
    }
    
    // Cache actions
    html += `
        <div class="cache-actions">
            <h4>🛠️ Cache Management</h4>
            <div class="cache-actions-grid">
                <button onclick="clearCache()" class="btn btn-warning">
                    🧹 Clear Old Cache (90+ days)
                </button>
                <button onclick="warmCache()" class="btn btn-info">
                    🔥 Warm Cache
                </button>
                <button onclick="viewCacheStats()" class="btn btn-secondary">
                    🔄 Refresh Stats
                </button>
            </div>
        </div>
    `;
    
    statsContainer.innerHTML = html;
}

function toggleAdminPanel() {
    const panel = document.getElementById('admin-panel');
    if (panel) {
        panel.classList.toggle('show');
    }
}

// Job action functions
async function retryJob(jobId) {
    if (!confirm(`Are you sure you want to retry job ${jobId}?`)) return;
    
    // Check if user has remaining uses for retry
    if (currentUser && currentUser.remaining_uses === 0) {
        showError('You have no remaining uses. Cannot retry job.');
        return;
    }
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}/retry`, {
            method: 'POST'
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.status === 'success') {
            showSuccess(`Job ${jobId} retry initiated`);
            // Update remaining uses if provided
            if (currentUser && currentUser.remaining_uses > 0) {
                currentUser.remaining_uses -= 1;
                updateUserStatusBar(currentUser);
            }
            await loadJobs();
        } else {
            showError(result.message || 'Failed to retry job');
        }
    } catch (error) {
        console.error('Error retrying job:', error);
        showError('Failed to retry job: ' + error.message);
    }
}

async function deleteJob(jobId) {
    if (!confirm(`Are you sure you want to delete job ${jobId}?`)) return;
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}`, {
            method: 'DELETE'
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.status === 'success') {
            showSuccess(`Job ${jobId} deleted`);
            await loadJobs();
        } else {
            showError(result.message || 'Failed to delete job');
        }
    } catch (error) {
        console.error('Error deleting job:', error);
        showError('Failed to delete job: ' + error.message);
    }
}

async function reviewLyrics(jobId) {
    try {
        showNotification('Starting review server...', 'info');
        
        // Call the start review endpoint
        const response = await authenticatedFetch(`${API_BASE_URL}/review/${jobId}/start`, {
            method: 'POST'
        });
        
        if (!response) return; // Auth failed, already handled
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const result = await response.json();
        
        if (result.review_url) {
            showNotification('Review server started! Opening review interface...', 'success');
            // Open the review interface
            window.open(result.review_url, '_blank');
        } else {
            throw new Error('No review URL returned from server');
        }
    } catch (error) {
        console.error('Error starting review:', error);
        showNotification(`Error starting review: ${error.message}`, 'error');
    }
}

async function completeReview(jobId) {
    if (!confirm('Complete the review without additional corrections? This will generate the video and move to instrumental selection.')) {
        return;
    }
    
    try {
        showInfo('Completing review and generating video...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/corrections/${jobId}/complete`, {
            method: 'POST',
            body: JSON.stringify({
                corrected_data: {} // Empty object indicates no additional corrections
            })
        });
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const result = await response.json();
            showSuccess(result.message);
            await loadJobs(); // Refresh to show updated status
        } else {
            const error = await response.json();
            showError('Error completing review: ' + error.detail);
        }
    } catch (error) {
        console.error('Error completing review:', error);
        showError('Error completing review: ' + error.message);
    }
}

function downloadVideo(jobId) {
    const token = getAuthToken();
    if (!token) {
        showError('Authentication required for downloads');
        return;
    }
    
    // Create download URL with authentication token
    const url = `${API_BASE_URL}/jobs/${jobId}/download?token=${encodeURIComponent(token)}`;
    window.open(url, '_blank');
}

function downloadAll(jobId) {
    // Use the existing downloadAllFiles function
    downloadAllFiles(jobId);
}

// Form submission
async function submitJob() {
    // Check if user is authenticated and has remaining uses
    if (!currentUser) {
        showError('You must be logged in to submit jobs');
        return;
    }
    
    if (currentUser.remaining_uses === 0) {
        showError('You have no remaining uses. Please contact support or purchase additional access.');
        return;
    }
    
    // Determine which input mode is active
    const fileMode = document.getElementById('file-upload-mode');
    const youtubeMode = document.getElementById('youtube-url-mode');
    const isYouTubeMode = youtubeMode.classList.contains('active');
    
    const submitBtn = document.querySelector('.submit-btn');
    const originalText = submitBtn.textContent;
    
    try {
        submitBtn.disabled = true;
        
        if (isYouTubeMode) {
            // Handle YouTube URL submission
            await submitYouTubeJob(submitBtn);
        } else {
            // Handle file upload submission
            await submitFileJob(submitBtn);
        }
        
    } catch (error) {
        console.error('Error submitting job:', error);
        showError('Failed to submit job: ' + error.message);
    } finally {
        submitBtn.textContent = originalText;
        submitBtn.disabled = false;
    }
}

async function submitYouTubeJob(submitBtn) {
    submitBtn.textContent = 'Processing URL...';
    
    // Get YouTube URL and validate
    const youtubeUrl = document.getElementById('youtube-url').value.trim();
    if (!youtubeUrl) {
        showError('Please enter a YouTube URL');
        return;
    }
    
    // Validate YouTube URL format
    const youtubePattern = /^https:\/\/(www\.)?(youtube\.com\/(watch\?v=|embed\/)|youtu\.be\/).+/;
    if (!youtubePattern.test(youtubeUrl)) {
        showError('Please enter a valid YouTube URL (e.g., https://www.youtube.com/watch?v=...)');
        return;
    }
    
    // Get artist and title values (required)
    const artist = document.getElementById('youtube-artist').value.trim();
    const title = document.getElementById('youtube-title').value.trim();
    
    if (!artist || !title) {
        showError('Please wait for metadata extraction to complete, or enter artist and title manually');
        return;
    }
    
    // Prepare form data (same as file upload jobs)
    const formData = new FormData();
    const stylesFile = document.getElementById('styles-file').files[0];
    const stylesArchive = document.getElementById('styles-archive').files[0];
    const customStylesVisible = document.getElementById('custom-styles-section').style.display !== 'none';
    
    formData.append('url', youtubeUrl);
    formData.append('artist', artist);
    formData.append('title', title);
    
    // Handle styles the same way as file upload jobs
    if (!customStylesVisible || (!stylesFile && !stylesArchive)) {
        try {
            console.log('Loading default Nomad styles for YouTube job...');
            // Load default styles automatically
            const [defaultStylesResponse, defaultArchiveResponse] = await Promise.all([
                fetch('./karaoke-prep-styles-nomad.json'),
                fetch('./nomadstyles.zip')
            ]);
            
            console.log('Default styles fetch results:', {
                stylesOk: defaultStylesResponse.ok,
                stylesStatus: defaultStylesResponse.status,
                archiveOk: defaultArchiveResponse.ok, 
                archiveStatus: defaultArchiveResponse.status
            });
            
            if (defaultStylesResponse.ok && defaultArchiveResponse.ok) {
                const defaultStylesJson = await defaultStylesResponse.text();
                const defaultArchiveBlob = await defaultArchiveResponse.blob();
                
                console.log('Default styles loaded:', {
                    stylesSize: defaultStylesJson.length,
                    archiveSize: defaultArchiveBlob.size
                });
                
                // Create default style files
                const defaultStylesFile = new File([new Blob([defaultStylesJson], { type: 'application/json' })], 'karaoke-prep-styles-nomad.json', { type: 'application/json' });
                const defaultArchiveFile = new File([defaultArchiveBlob], 'nomadstyles.zip', { type: 'application/zip' });
                
                formData.append('styles_file', defaultStylesFile);
                formData.append('styles_archive', defaultArchiveFile);
                console.log('✅ Default styles appended to form data for YouTube job');
            } else {
                throw new Error(`Failed to fetch default styles: styles=${defaultStylesResponse.status}, archive=${defaultArchiveResponse.status}`);
            }
        } catch (error) {
            console.error('Failed to load default styles:', error);
            showError('Failed to load default Nomad styles. Please check your internet connection and try again.');
            throw error; // Don't proceed without styles since backend now requires them
        }
    } else {
        // Use custom styles if provided
        if (stylesFile) {
            formData.append('styles_file', stylesFile);
        }
        
        if (stylesArchive) {
            formData.append('styles_archive', stylesArchive);
        }
    }
    
    // Create authenticated request headers for form data
    const authHeaders = {};
    const token = getAuthToken();
    if (token) {
        authHeaders['Authorization'] = `Bearer ${token}`;
    }
    
    const response = await fetch(`${API_BASE_URL}/submit-youtube`, {
        method: 'POST',
        headers: authHeaders,
        body: formData
    });
    
    if (!response.ok) {
        if (response.status === 401) {
            setAuthToken(null);
            currentUser = null;
            showAuthSection();
            showError('Session expired. Please log in again.');
            return;
        }
    }
    
    const result = await response.json();
    
    if (response.status === 200) {
        const usingCustomStyles = customStylesVisible && (stylesFile || stylesArchive);
        const stylesMessage = usingCustomStyles ? ' with custom styles' : ' with default Nomad styles';
        showSuccess(`YouTube job submitted successfully${stylesMessage}! Job ID: ${result.job_id}`);
        
        // Update user's remaining uses if provided
        if (result.remaining_uses !== undefined) {
            currentUser.remaining_uses = result.remaining_uses;
            updateUserStatusBar(currentUser);
        }
        
        // Clear form
        document.getElementById('youtube-url').value = '';
        document.getElementById('youtube-artist').value = '';
        document.getElementById('youtube-title').value = '';
        
        // Only clear custom styles if they were visible
        if (customStylesVisible) {
            document.getElementById('styles-file').value = '';
            document.getElementById('styles-archive').value = '';
        }
        
        // Reset fields to disabled state for next submission
        const artistField = document.getElementById('youtube-artist');
        const titleField = document.getElementById('youtube-title');
        if (artistField) {
            artistField.disabled = true;
            artistField.placeholder = 'Enter YouTube URL first to auto-populate';
        }
        if (titleField) {
            titleField.disabled = true;
            titleField.placeholder = 'Enter YouTube URL first to auto-populate';
        }
        
        // Refresh jobs list and handle post-submission tasks
        await handlePostSubmission();
        
    } else {
        // Handle specific error messages for YouTube issues
        if (result.message && (result.message.includes('blocked') || result.message.includes('bot'))) {
            showError(`${result.message}\n\nNote: YouTube access issues are being addressed by the admin. Please try again later.`);
        } else {
            showError(result.message || 'Failed to submit YouTube job');
        }
    }
}

async function submitFileJob(submitBtn) {
    submitBtn.textContent = 'Uploading...';
    
    // Prepare form data
    const formData = new FormData();
    const audioFile = document.getElementById('audio-file').files[0];
    const stylesFile = document.getElementById('styles-file').files[0];
    const stylesArchive = document.getElementById('styles-archive').files[0];
    const customStylesVisible = document.getElementById('custom-styles-section').style.display !== 'none';
    
    if (!audioFile) {
        showError('Please select an audio file');
        return;
    }
    
    formData.append('audio_file', audioFile);
    formData.append('artist', document.getElementById('artist').value);
    formData.append('title', document.getElementById('title').value);
    
    // If custom styles section is hidden or no custom styles are provided, use default styles
    if (!customStylesVisible || (!stylesFile && !stylesArchive)) {
        try {
            console.log('Loading default Nomad styles for file upload job...');
            // Load default styles automatically
            const [defaultStylesResponse, defaultArchiveResponse] = await Promise.all([
                fetch('./karaoke-prep-styles-nomad.json'),
                fetch('./nomadstyles.zip')
            ]);
            
            console.log('Default styles fetch results:', {
                stylesOk: defaultStylesResponse.ok,
                stylesStatus: defaultStylesResponse.status,
                archiveOk: defaultArchiveResponse.ok, 
                archiveStatus: defaultArchiveResponse.status
            });
            
            if (defaultStylesResponse.ok && defaultArchiveResponse.ok) {
                const defaultStylesJson = await defaultStylesResponse.text();
                const defaultArchiveBlob = await defaultArchiveResponse.blob();
                
                console.log('Default styles loaded:', {
                    stylesSize: defaultStylesJson.length,
                    archiveSize: defaultArchiveBlob.size
                });
                
                // Create default style files
                const defaultStylesFile = new File([new Blob([defaultStylesJson], { type: 'application/json' })], 'karaoke-prep-styles-nomad.json', { type: 'application/json' });
                const defaultArchiveFile = new File([defaultArchiveBlob], 'nomadstyles.zip', { type: 'application/zip' });
                
                formData.append('styles_file', defaultStylesFile);
                formData.append('styles_archive', defaultArchiveFile);
                console.log('✅ Default styles appended to form data for file upload job');
            } else {
                throw new Error(`Failed to fetch default styles: styles=${defaultStylesResponse.status}, archive=${defaultArchiveResponse.status}`);
            }
        } catch (error) {
            console.error('Failed to load default styles:', error);
            showError('Failed to load default Nomad styles. Please check your internet connection and try again.');
            throw error; // Don't proceed without styles since backend now requires them
        }
    } else {
        // Use custom styles if provided
        if (stylesFile) {
            formData.append('styles_file', stylesFile);
        }
        
        if (stylesArchive) {
            formData.append('styles_archive', stylesArchive);
        }
    }
    
    // Create authenticated request headers for form data
    const authHeaders = {};
    const token = getAuthToken();
    if (token) {
        authHeaders['Authorization'] = `Bearer ${token}`;
    }
    
    const response = await fetch(`${API_BASE_URL}/submit-file`, {
        method: 'POST',
        headers: authHeaders,
        body: formData
    });
    
    if (!response.ok) {
        if (response.status === 401) {
            setAuthToken(null);
            currentUser = null;
            showAuthSection();
            showError('Session expired. Please log in again.');
            return;
        }
    }
    
    const result = await response.json();
    
    if (response.status === 200) {
        const usingCustomStyles = customStylesVisible && (stylesFile || stylesArchive);
        const stylesMessage = usingCustomStyles ? ' with custom styles' : ' with default Nomad styles';
        showSuccess(`Job submitted successfully${stylesMessage}! Job ID: ${result.job_id}`);
        
        // Update user's remaining uses if provided
        if (result.remaining_uses !== undefined) {
            currentUser.remaining_uses = result.remaining_uses;
            updateUserStatusBar(currentUser);
        }
        
        // Clear form
        document.getElementById('audio-file').value = '';
        document.getElementById('artist').value = '';
        document.getElementById('title').value = '';
        
        // Only clear custom styles if they were visible
        if (customStylesVisible) {
            document.getElementById('styles-file').value = '';
            document.getElementById('styles-archive').value = '';
        }
        
        // Refresh jobs list and handle post-submission tasks
        await handlePostSubmission();
        
    } else {
        showError(result.message || 'Failed to submit job');
    }
}

async function handlePostSubmission() {
    // Refresh jobs list immediately and scroll to it
    showInfo('Refreshing job list...');
    const jobs = await loadJobs();
    
    if (jobs) {        
        // Auto-refresh job data after 2, 5, and 10 seconds to ensure the new job shows up
        setTimeout(async () => {
            await loadJobs();
        }, 2000);
        
        setTimeout(async () => {
            await loadJobs();
        }, 5000);
        
        setTimeout(async () => {
            await loadJobs();
        }, 10000);
        
        // Show info about what happens next
        setTimeout(() => {
            showInfo('Your job is now processing. The status will update automatically as it progresses.');
        }, 2000);
        
        // Enable auto-refresh if not already enabled
        const autoRefreshCheckbox = document.getElementById('auto-refresh');
        if (autoRefreshCheckbox && !autoRefreshCheckbox.checked) {
            autoRefreshCheckbox.checked = true;
            startAutoRefresh();
            setTimeout(() => {
                showInfo('Auto-refresh enabled to track your job progress.');
            }, 4000);
        }
    }
}

// Input mode switching functions
function switchInputMode(mode) {
    const fileTab = document.getElementById('file-mode-tab');
    const youtubeTab = document.getElementById('youtube-mode-tab');
    const fileSection = document.getElementById('file-upload-mode');
    const youtubeSection = document.getElementById('youtube-url-mode');
    
    // Get form elements
    const artistField = document.getElementById('artist');
    const titleField = document.getElementById('title');
    const audioField = document.getElementById('audio-file');
    const youtubeUrlField = document.getElementById('youtube-url');
    const youtubeArtistField = document.getElementById('youtube-artist');
    const youtubeTitleField = document.getElementById('youtube-title');
    
    if (mode === 'file') {
        // Switch to file upload mode
        fileTab.classList.add('active');
        youtubeTab.classList.remove('active');
        fileSection.classList.add('active');
        youtubeSection.classList.remove('active');
        
        // Enable required attributes for file mode
        if (artistField) artistField.required = true;
        if (titleField) titleField.required = true;
        if (audioField) audioField.required = true;
        
        // Disable required attributes for YouTube mode
        if (youtubeUrlField) youtubeUrlField.required = false;
        if (youtubeArtistField) youtubeArtistField.required = false;
        if (youtubeTitleField) youtubeTitleField.required = false;
        
        // Clear YouTube form
        document.getElementById('youtube-url').value = '';
        document.getElementById('youtube-artist').value = '';
        document.getElementById('youtube-title').value = '';
        
        // Re-enable all fields and submit button when switching to file mode
        const youtubeArtistFieldForReset = document.getElementById('youtube-artist');
        const youtubeTitleFieldForReset = document.getElementById('youtube-title');
        const submitBtn = document.querySelector('.submit-btn');
        if (youtubeArtistFieldForReset) youtubeArtistFieldForReset.disabled = false;
        if (youtubeTitleFieldForReset) youtubeTitleFieldForReset.disabled = false;
        if (submitBtn) submitBtn.disabled = false;
        
        // Clear metadata status
        const metadataStatus = document.getElementById('metadata-status');
        if (metadataStatus) {
            metadataStatus.textContent = '';
            metadataStatus.className = 'metadata-status';
        }
        
    } else if (mode === 'youtube') {
        // Switch to YouTube URL mode
        youtubeTab.classList.add('active');
        fileTab.classList.remove('active');
        youtubeSection.classList.add('active');
        fileSection.classList.remove('active');
        
        // Disable required attributes for file mode
        if (artistField) artistField.required = false;
        if (titleField) titleField.required = false;
        if (audioField) audioField.required = false;
        
        // Enable required attributes for YouTube mode
        if (youtubeUrlField) youtubeUrlField.required = true;
        if (youtubeArtistField) youtubeArtistField.required = true;
        if (youtubeTitleField) youtubeTitleField.required = true;
        
        // Initially disable artist/title fields and submit button until metadata is extracted
        if (youtubeArtistField) {
            youtubeArtistField.disabled = true;
            youtubeArtistField.placeholder = 'Enter YouTube URL first to auto-populate';
        }
        if (youtubeTitleField) {
            youtubeTitleField.disabled = true;
            youtubeTitleField.placeholder = 'Enter YouTube URL first to auto-populate';
        }
        
        // Disable submit button
        const submitBtn = document.querySelector('.submit-btn');
        if (submitBtn) {
            submitBtn.disabled = true;
        }
        
        // Clear file upload form
        document.getElementById('audio-file').value = '';
        document.getElementById('artist').value = '';
        document.getElementById('title').value = '';
    }
}



// Toggle custom styles section
function toggleCustomStyles() {
    const customSection = document.getElementById('custom-styles-section');
    const toggleBtn = document.getElementById('customize-styles-btn');
    
    if (customSection.style.display === 'none') {
        customSection.style.display = 'block';
        toggleBtn.textContent = '🎨 Use Default Styles';
        toggleBtn.title = 'Hide custom styles and use default Nomad styles';
    } else {
        customSection.style.display = 'none';
        toggleBtn.textContent = '🎛️ Customize Styles';
        toggleBtn.title = 'Show custom styles options';
        
        // Clear any selected custom files when hiding
        document.getElementById('styles-file').value = '';
        document.getElementById('styles-archive').value = '';
    }
}

// YouTube metadata auto-population
let metadataExtractionTimeout = null;

function handleYouTubeUrlChange() {
    const urlInput = document.getElementById('youtube-url');
    const url = urlInput.value.trim();
    
    // Clear any existing timeout
    if (metadataExtractionTimeout) {
        clearTimeout(metadataExtractionTimeout);
    }
    
    // Get field references
    const artistField = document.getElementById('youtube-artist');
    const titleField = document.getElementById('youtube-title');
    const metadataStatus = document.getElementById('metadata-status');
    const submitBtn = document.querySelector('.submit-btn');
    
    // Clear artist/title fields and status when URL changes
    artistField.value = '';
    titleField.value = '';
    metadataStatus.textContent = '';
    metadataStatus.className = 'metadata-status';
    
    // Disable fields and submit button when URL changes
    artistField.disabled = true;
    titleField.disabled = true;
    if (submitBtn) submitBtn.disabled = true;
    
    // Only proceed if URL looks like YouTube
    const youtubePattern = /^https:\/\/(www\.)?(youtube\.com\/(watch\?v=|embed\/)|youtu\.be\/).+/;
    if (!url || !youtubePattern.test(url)) {
        // If URL is empty or invalid, update placeholders
        artistField.placeholder = url ? 'Invalid YouTube URL' : 'Enter YouTube URL first to auto-populate';
        titleField.placeholder = url ? 'Invalid YouTube URL' : 'Enter YouTube URL first to auto-populate';
        return;
    }
    
    // Update placeholders to show loading state
    artistField.placeholder = 'Extracting from YouTube...';
    titleField.placeholder = 'Extracting from YouTube...';
    
    // Debounce the API call - wait 1 second after user stops typing
    metadataExtractionTimeout = setTimeout(() => {
        extractYouTubeMetadata(url);
    }, 1000);
}

async function extractYouTubeMetadata(url) {
    const loadingIndicator = document.getElementById('metadata-loading');
    const metadataStatus = document.getElementById('metadata-status');
    const artistField = document.getElementById('youtube-artist');
    const titleField = document.getElementById('youtube-title');
    const submitBtn = document.querySelector('.submit-btn');
    
    try {
        // Show loading indicator
        loadingIndicator.style.display = 'block';
        metadataStatus.textContent = '';
        
        const response = await authenticatedFetch(`${API_BASE_URL}/youtube/metadata`, {
            method: 'POST',
            body: JSON.stringify({ url: url })
        });
        
        if (!response) {
            // Auth failed, already handled by authenticatedFetch
            loadingIndicator.style.display = 'none';
            enableFieldsAfterExtraction(artistField, titleField, submitBtn);
            return;
        }
        
        const result = await response.json();
        
        if (result.success) {
            // Populate the fields with extracted metadata
            artistField.value = result.artist || '';
            titleField.value = result.title || '';
            
            // Update placeholders to show they can be edited
            artistField.placeholder = 'Auto-extracted from YouTube - edit if needed';
            titleField.placeholder = 'Auto-extracted from YouTube - edit if needed';
            
            // Show success status
            metadataStatus.textContent = '✅ Metadata extracted successfully';
            metadataStatus.className = 'metadata-status success';
            
            // Clear success message after 3 seconds
            setTimeout(() => {
                if (metadataStatus.textContent === '✅ Metadata extracted successfully') {
                    metadataStatus.textContent = '';
                    metadataStatus.className = 'metadata-status';
                }
            }, 3000);
            
        } else {
            // Handle extraction errors
            let errorMessage = result.message || 'Failed to extract metadata';
            
            if (result.error_type === 'bot_detection') {
                errorMessage = '⚠️ YouTube blocked access. Contact admin to update cookies.';
            } else {
                errorMessage = `❌ ${errorMessage}`;
            }
            
            metadataStatus.textContent = errorMessage;
            metadataStatus.className = 'metadata-status error';
            
            // Show user they can enter manually
            artistField.placeholder = 'Enter artist name manually';
            titleField.placeholder = 'Enter song title manually';
        }
        
    } catch (error) {
        console.error('Error extracting YouTube metadata:', error);
        metadataStatus.textContent = '❌ Error extracting metadata - please enter manually';
        metadataStatus.className = 'metadata-status error';
        
        artistField.placeholder = 'Enter artist name manually';
        titleField.placeholder = 'Enter song title manually';
        
    } finally {
        loadingIndicator.style.display = 'none';
        // Always enable fields after extraction completes (success or failure)
        enableFieldsAfterExtraction(artistField, titleField, submitBtn);
    }
}

function enableFieldsAfterExtraction(artistField, titleField, submitBtn) {
    // Enable the artist and title fields
    if (artistField) artistField.disabled = false;
    if (titleField) titleField.disabled = false;
    
    // Enable submit button
    if (submitBtn) submitBtn.disabled = false;
}

// Load default styles function
async function loadDefaultStyles() {
    try {
        const [stylesResponse, archiveResponse] = await Promise.all([
            fetch('./karaoke-prep-styles-nomad.json'),
            fetch('./nomadstyles.zip')
        ]);
        
        if (!stylesResponse.ok) {
            throw new Error('Default styles file not found');
        }
        if (!archiveResponse.ok) {
            throw new Error('Default styles archive not found');
        }
        
        const stylesJson = await stylesResponse.text();
        const archiveBlob = await archiveResponse.blob();
        
        // Create files from the data
        const stylesFile = new File([new Blob([stylesJson], { type: 'application/json' })], 'karaoke-prep-styles-nomad.json', { type: 'application/json' });
        const archiveFile = new File([archiveBlob], 'nomadstyles.zip', { type: 'application/zip' });
        
        // Set the file inputs
        const stylesInput = document.getElementById('styles-file');
        const archiveInput = document.getElementById('styles-archive');
        
        const stylesDataTransfer = new DataTransfer();
        stylesDataTransfer.items.add(stylesFile);
        stylesInput.files = stylesDataTransfer.files;
        
        const archiveDataTransfer = new DataTransfer();
        archiveDataTransfer.items.add(archiveFile);
        archiveInput.files = archiveDataTransfer.files;
        
        showSuccess('Default Nomad styles and assets loaded successfully!');
        
    } catch (error) {
        console.error('Error loading default styles:', error);
        showError('Failed to load default styles: ' + error.message);
    }
}

// Load example data function
async function loadExampleData() {
    try {
        const audioResponse = await fetch('./waterloo30sec.flac');
        
        if (!audioResponse.ok) {
            throw new Error('Example audio file not found');
        }
        
        const audioBlob = await audioResponse.blob();
        
        // Create audio file from the data
        const audioFile = new File([audioBlob], 'waterloo30sec.flac', { type: 'audio/flac' });
        
        // Set the audio file input
        const audioInput = document.getElementById('audio-file');
        const audioDataTransfer = new DataTransfer();
        audioDataTransfer.items.add(audioFile);
        audioInput.files = audioDataTransfer.files;
        
        // Pre-fill the form with example data
        document.getElementById('artist').value = 'ABBA';
        document.getElementById('title').value = 'Waterloo';
        
        showSuccess('Example data loaded successfully! Audio file and metadata are ready to submit with default Nomad styles.');
        
    } catch (error) {
        console.error('Error loading example data:', error);
        showError('Failed to load example data: ' + error.message);
    }
}

// Utility functions
function parseServerTime(timestamp) {
    // Properly parse server timestamps that are in UTC and convert to local time
    if (!timestamp) return new Date();
    
    // Convert to string if it's not already
    const timestampStr = String(timestamp);
    
    try {
        // Handle various timestamp formats
        let date;
        
        // If timestamp already has timezone info (Z, +, or -), parse directly
        if (timestampStr.includes('Z') || timestampStr.includes('+') || 
            (timestampStr.includes('-') && timestampStr.lastIndexOf('-') > 10)) {
            date = new Date(timestampStr);
        } else {
            // No timezone info - assume UTC and add 'Z'
            date = new Date(timestampStr + 'Z');
        }
        
        // Validate the parsed date
        if (isNaN(date.getTime())) {
            console.warn('Invalid timestamp parsed:', timestampStr);
            return new Date(); // Return current time as fallback
        }
        
        return date;
    } catch (error) {
        console.error('Error parsing timestamp:', timestampStr, error);
        return new Date(); // Return current time as fallback
    }
}

function formatStatus(status) {
    const statusMap = {
        'queued': 'Queued',
        'processing': 'Processing',
        'processing_audio': 'Processing Audio',
        'transcribing': 'Transcribing Lyrics',
        'awaiting_review': 'Awaiting Review',
        'reviewing': 'Reviewing',
        'ready_for_finalization': 'Ready for Finalization',
        'rendering': 'Rendering Video',
        'finalizing': 'Finalizing',
        'complete': 'Complete',
        'error': 'Error',
        'timeout': 'Timed Out'
    };
    return statusMap[status] || status;
}

function formatTimestamp(timestamp) {
    try {
        const date = parseServerTime(timestamp);
        
        // Validate the parsed date
        if (isNaN(date.getTime())) {
            console.warn('Invalid timestamp for formatting:', timestamp);
            return 'Invalid Time';
        }
        
        // Format with explicit local timezone display options
        return date.toLocaleString([], {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
    } catch (error) {
        console.error('Error formatting timestamp:', timestamp, error);
        return 'Error';
    }
}

function calculateDuration(createdAt) {
    if (!createdAt) return 'Unknown';
    
    try {
        const now = new Date();
        const startTime = parseServerTime(createdAt);
        const diffMs = now - startTime;
        
        // Debug logging for timezone issues
        if (diffMs < 0) {
            console.warn('Negative duration in calculateDuration:', {
                startTime: startTime.toISOString(),
                now: now.toISOString(),
                originalTimestamp: createdAt,
                diffMs,
                userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone
            });
            return '0s'; // Handle negative durations
        }
        
        const seconds = Math.floor(diffMs / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);
        
        if (days > 0) {
            return `${days}d ${hours % 24}h`;
        } else if (hours > 0) {
            return `${hours}h ${minutes % 60}m`;
        } else if (minutes > 0) {
            return `${minutes}m ${seconds % 60}s`;
        } else {
            return `${seconds}s`;
        }
    } catch (error) {
        console.error('Error in calculateDuration:', error, createdAt);
        return 'Error';
    }
}

function formatDurationWithStatus(job) {
    const duration = getTotalJobDuration(job); // Use the corrected duration calculation
    const status = job.status || 'unknown';
    
    // Different duration labels based on status
    if (status === 'queued') {
        return `⏱️ ${duration} waiting`;
    } else if (['processing_audio', 'transcribing', 'rendering'].includes(status)) {
        return `⏳ ${duration} running`;
    } else if (status === 'awaiting_review') {
        return `⏸️ ${duration} awaiting review`;
    } else if (status === 'complete') {
        return `✅ ${duration} total`;
    } else if (status === 'error') {
        return `❌ ${duration} before error`;
    } else {
        return `📅 ${duration}`;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showSuccess(message) {
    showNotification(message, 'success');
}

function showError(message) {
    showNotification(message, 'error');
}

function showInfo(message, timeout = 5000) {
    showNotification(message, 'info', timeout);
}

function hideInfo() {
    // Hide any currently displayed info notifications
    const notifications = document.querySelectorAll('.notification.info');
    notifications.forEach(notification => {
        notification.remove();
    });
}

function showNotification(message, type, timeout = 5000) {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    // Add to proper notifications container
    const notificationsContainer = document.getElementById('notifications');
    if (notificationsContainer) {
        notificationsContainer.appendChild(notification);
    } else {
        // Fallback to body if container doesn't exist
        document.body.appendChild(notification);
    }
    
    // Auto-remove after specified timeout
    setTimeout(() => {
        notification.remove();
    }, timeout);
}

// Font size and scroll control functions
const fontSizeClasses = ['font-xs', 'font-sm', 'font-md', 'font-lg', 'font-xl', 'font-xxl'];

function updateLogsFontSize() {
    const modalLogs = document.getElementById('modal-logs');
    if (!modalLogs) return;
    
    // Remove all font size classes
    fontSizeClasses.forEach(cls => modalLogs.classList.remove(cls));
    
    // Add current font size class
    modalLogs.classList.add(fontSizeClasses[logFontSizeIndex]);
}

function increaseFontSize() {
    if (logFontSizeIndex < fontSizeClasses.length - 1) {
        logFontSizeIndex++;
        updateLogsFontSize();
    }
}

function decreaseFontSize() {
    if (logFontSizeIndex > 0) {
        logFontSizeIndex--;
        updateLogsFontSize();
    }
}

function scrollToBottom() {
    const modalLogs = document.getElementById('modal-logs');
    if (modalLogs) {
        modalLogs.scrollTop = modalLogs.scrollHeight;
    }
}

function toggleAutoScroll() {
    autoScrollEnabled = !autoScrollEnabled;
    
    const autoScrollBtn = document.getElementById('auto-scroll-btn');
    if (autoScrollBtn) {
        if (autoScrollEnabled) {
            autoScrollBtn.classList.add('toggle-active');
            autoScrollBtn.textContent = '🔄 Auto';
            autoScrollBtn.title = 'Auto-scroll enabled - click to disable';
            // Scroll to bottom when enabling auto-scroll
            scrollToBottom();
        } else {
            autoScrollBtn.classList.remove('toggle-active');
            autoScrollBtn.textContent = '⏸️ Manual';
            autoScrollBtn.title = 'Auto-scroll disabled - click to enable';
        }
    }
}

function copyLogsToClipboard() {
    console.log('Copy logs function called'); // Debug log
    
    const modalLogs = document.getElementById('modal-logs');
    const jobIdElement = document.getElementById('modal-job-id');
    
    console.log('Modal logs element:', modalLogs); // Debug log
    console.log('Job ID element:', jobIdElement); // Debug log
    
    if (!modalLogs) {
        console.error('Modal logs element not found');
        showError('Unable to access logs for copying');
        return;
    }
    
    // Get job ID from element or use current tail job ID as fallback
    let jobId = 'unknown';
    if (jobIdElement && jobIdElement.textContent) {
        jobId = jobIdElement.textContent.trim();
    } else if (currentTailJobId) {
        jobId = currentTailJobId;
        console.log('Using fallback job ID from currentTailJobId:', jobId);
    } else {
        console.log('Job ID not found, using default');
    }
    
    console.log('Using job ID:', jobId); // Debug log
    
    try {
        // Get all log entries and format them as plain text
        const logEntries = modalLogs.querySelectorAll('.log-entry');
        console.log('Found log entries:', logEntries.length); // Debug log
        
        if (logEntries.length === 0) {
            // Try alternative selectors in case the structure is different
            const allText = modalLogs.textContent || modalLogs.innerText;
            if (!allText.trim()) {
                showError('No logs available to copy');
                return;
            }
            
            // If no structured log entries, just copy all text content
            const now = new Date();
            const simpleHeader = `=== Karaoke Generator Job ${jobId} Logs ===\n` +
                               `Exported: ${now.toLocaleString()}\n` +
                               `${'='.repeat(50)}\n\n`;
            
            const fullText = simpleHeader + allText;
            
            copyTextToClipboard(fullText, `Copied logs content to clipboard`);
            return;
        }
        
        // Create header with job info and timestamp
        const now = new Date();
        const exportHeader = `=== Karaoke Generator Job ${jobId} Logs ===\n` +
                           `Exported: ${now.toLocaleString()}\n` +
                           `Total log entries: ${logEntries.length}\n` +
                           `${'='.repeat(50)}\n\n`;
        
        // Extract text content from each log entry
        const logText = Array.from(logEntries).map(entry => {
            const timestamp = entry.querySelector('.log-timestamp')?.textContent || '';
            const level = entry.querySelector('.log-level')?.textContent || '';
            const message = entry.querySelector('.log-message')?.textContent || '';
            
            return `${timestamp} ${level.padEnd(8)} ${message}`;
        }).join('\n');
        
        const fullLogText = exportHeader + logText;
        
        copyTextToClipboard(fullLogText, `Copied ${logEntries.length} log entries to clipboard`);
        
    } catch (error) {
        console.error('Error copying logs to clipboard:', error);
        showError('Failed to copy logs to clipboard: ' + error.message);
    }
}

function copyTextToClipboard(text, successMessage) {
    // Try modern clipboard API first
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            console.log('Clipboard copy successful (modern API)');
            showSuccess(successMessage);
            updateCopyButtonFeedback();
        }).catch(error => {
            console.error('Modern clipboard API failed:', error);
            // Fall back to older method
            fallbackCopyTextToClipboard(text, successMessage);
        });
    } else {
        console.log('Modern clipboard API not available, using fallback');
        fallbackCopyTextToClipboard(text, successMessage);
    }
}

function fallbackCopyTextToClipboard(text, successMessage) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
        const successful = document.execCommand('copy');
        document.body.removeChild(textArea);
        
        if (successful) {
            console.log('Clipboard copy successful (fallback method)');
            showSuccess(successMessage + ' (fallback method)');
            updateCopyButtonFeedback();
        } else {
            console.error('Fallback copy command failed');
            showError('Failed to copy to clipboard');
        }
    } catch (error) {
        console.error('Fallback copy failed:', error);
        document.body.removeChild(textArea);
        showError('Copy to clipboard not supported by browser');
    }
}

function updateCopyButtonFeedback() {
    const copyBtn = document.querySelector('button[onclick="copyLogsToClipboard()"]');
    if (copyBtn) {
        const originalText = copyBtn.textContent;
        const originalClass = copyBtn.className;
        copyBtn.textContent = '✅ Copied!';
        copyBtn.className = originalClass + ' toggle-active';
        
        setTimeout(() => {
            copyBtn.textContent = originalText;
            copyBtn.className = originalClass;
        }, 2000);
    }
}

function copyTrackInfo(trackInfo) {
    try {
        copyTextToClipboard(trackInfo, `Track info copied to clipboard: ${trackInfo}`);
        
        // Find and update the copy button that was clicked
        const copyBtn = event.target;
        if (copyBtn) {
            const originalText = copyBtn.textContent;
            copyBtn.textContent = '✅';
            copyBtn.style.backgroundColor = '#28a745';
            copyBtn.style.color = 'white';
            
            setTimeout(() => {
                copyBtn.textContent = originalText;
                copyBtn.style.backgroundColor = '';
                copyBtn.style.color = '';
            }, 1500);
        }
    } catch (error) {
        console.error('Error copying track info:', error);
        showError('Failed to copy track info to clipboard');
    }
}

// Files modal functions
async function showFilesModal(jobId) {
    try {
        showInfo('Loading files...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}/files`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const filesData = await response.json();
        
        // Create and show the files modal
        createFilesModal(filesData);
        
    } catch (error) {
        console.error('Error loading files:', error);
        showError(`Error loading files: ${error.message}`);
    }
}

function createFilesModal(filesData) {
    const modalHtml = `
        <div id="files-modal" class="modal">
            <div class="modal-content files-modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">📁 Files for ${filesData.artist} - ${filesData.title}</h3>
                    <div class="modal-controls">
                        <button onclick="downloadAllFiles('${filesData.job_id}')" class="modal-control-btn primary" title="Download all files as ZIP">
                            📦 Download All (${filesData.total_size_mb} MB)
                        </button>
                        <button onclick="closeFilesModal()" class="modal-close">✕</button>
                    </div>
                </div>
                <div class="modal-body">
                    <div class="files-summary">
                        <div class="files-stats">
                            <span class="files-stat"><strong>${filesData.total_files}</strong> files</span>
                            <span class="files-stat"><strong>${filesData.total_size_mb} MB</strong> total</span>
                            <span class="files-stat">Status: <strong>${formatStatus(filesData.status)}</strong></span>
                        </div>
                    </div>
                    
                    <div class="files-categories">
                        ${createFilesCategoriesHtml(filesData)}
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Remove any existing files modal
    const existingModal = document.getElementById('files-modal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Show modal
    const modal = document.getElementById('files-modal');
    modal.style.display = 'flex';
    
    // Add click outside to close
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeFilesModal();
        }
    });
}

function createFilesCategoriesHtml(filesData) {
    if (!filesData.categories || Object.keys(filesData.categories).length === 0) {
        return '<p class="no-files">No files found for this job.</p>';
    }
    
    let html = '';
    
    Object.entries(filesData.categories).forEach(([categoryId, category]) => {
        html += `
            <div class="file-category">
                <div class="file-category-header">
                    <h4>${category.name}</h4>
                    <span class="file-category-count">${category.count} files</span>
                </div>
                <p class="file-category-description">${category.description}</p>
                <div class="file-category-files">
                    ${category.files.map(file => createFileItemHtml(filesData.job_id, file)).join('')}
                </div>
            </div>
        `;
    });
    
    return html;
}

function createFileItemHtml(jobId, file) {
    const iconClass = getFileIcon(file.mime_type);
    const sizeDisplay = file.size_mb > 0.1 ? `${file.size_mb} MB` : `${Math.round(file.size / 1024)} KB`;
    
    return `
        <div class="file-item">
            <div class="file-info">
                <div class="file-name">
                    <span class="file-icon">${iconClass}</span>
                    <span class="file-name-text">${file.name}</span>
                </div>
                <div class="file-details">
                    <span class="file-size">${sizeDisplay}</span>
                    <span class="file-date">${formatFileDate(file.modified)}</span>
                </div>
            </div>
            <div class="file-actions">
                ${createFileActionButtons(jobId, file)}
            </div>
        </div>
    `;
}

function createFileActionButtons(jobId, file) {
    const buttons = [];
    
    // Download button
    buttons.push(`
        <button onclick="downloadFile('${jobId}', '${escapeHtml(file.path)}', '${escapeHtml(file.name)}')" 
                class="btn btn-sm btn-primary" title="Download this file">
            📥 Download
        </button>
    `);
    
    // Preview button for videos
    if (file.mime_type.startsWith('video/')) {
        buttons.push(`
            <button onclick="previewVideo('${jobId}', '${escapeHtml(file.path)}', '${escapeHtml(file.name)}')" 
                    class="btn btn-sm btn-secondary" title="Preview video">
                ▶️ Preview
            </button>
        `);
    }
    
    // Preview button for audio
    if (file.mime_type.startsWith('audio/')) {
        buttons.push(`
            <button onclick="previewAudio('${jobId}', '${escapeHtml(file.path)}', '${escapeHtml(file.name)}')" 
                    class="btn btn-sm btn-secondary" title="Preview audio">
                🔊 Preview
            </button>
        `);
    }
    
    return buttons.join(' ');
}

function getFileIcon(mimeType) {
    if (mimeType.startsWith('video/')) return '🎬';
    if (mimeType.startsWith('audio/')) return '🎵';
    if (mimeType.startsWith('image/')) return '🖼️';
    if (mimeType === 'application/zip') return '📦';
    if (mimeType === 'text/plain') return '📄';
    if (mimeType === 'application/json') return '📋';
    return '📁';
}

function formatFileDate(dateString) {
    try {
        const date = parseServerTime(dateString);
        
        if (isNaN(date.getTime())) {
            console.warn('Invalid file date:', dateString);
            return 'Invalid Date';
        }
        
        return date.toLocaleDateString([], {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
        }) + ' ' + date.toLocaleTimeString([], {
            hour: '2-digit', 
            minute: '2-digit',
            hour12: false
        });
    } catch (error) {
        console.error('Error formatting file date:', dateString, error);
        return 'Error';
    }
}

function closeFilesModal() {
    const modal = document.getElementById('files-modal');
    if (modal) {
        modal.remove();
    }
}

// File action functions
async function downloadFile(jobId, filePath, fileName) {
    try {
        const token = getAuthToken();
        if (!token) {
            showError('Authentication required for downloads');
            return;
        }
        
        // Create download URL with authentication token
        const url = `${API_BASE_URL}/jobs/${jobId}/files/${encodeURIComponent(filePath)}?token=${encodeURIComponent(token)}`;
        
        // Create a link and trigger download
        const link = document.createElement('a');
        link.href = url;
        link.download = fileName;
        link.target = '_blank'; // Open in new tab to handle any potential auth redirects
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        showSuccess(`Downloading ${fileName}...`);
        
    } catch (error) {
        console.error('Error downloading file:', error);
        showError(`Error downloading file: ${error.message}`);
    }
}

async function downloadAllFiles(jobId) {
    try {
        const token = getAuthToken();
        if (!token) {
            showError('Authentication required for downloads');
            return;
        }
        
        // Create download URL with authentication token
        const url = `${API_BASE_URL}/jobs/${jobId}/download-all?token=${encodeURIComponent(token)}`;
        
        // Create a link and trigger download
        const link = document.createElement('a');
        link.href = url;
        link.download = `karaoke-${jobId}-complete.zip`;
        link.target = '_blank'; // Open in new tab to handle any potential auth redirects
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        showSuccess('Downloading complete package...');
        
    } catch (error) {
        console.error('Error downloading all files:', error);
        showError(`Error downloading files: ${error.message}`);
    }
}

function previewVideo(jobId, filePath, fileName) {
    const token = getAuthToken();
    if (!token) {
        showError('Authentication required for video preview');
        return;
    }
    
    // Create authenticated video URL
    const videoUrl = `${API_BASE_URL}/jobs/${jobId}/files/${encodeURIComponent(filePath)}?token=${encodeURIComponent(token)}`;
    
    const previewHtml = `
        <div id="video-preview-modal" class="modal">
            <div class="modal-content video-preview-content">
                <div class="modal-header">
                    <h3 class="modal-title">🎬 ${fileName}</h3>
                    <div class="modal-controls">
                        <button onclick="closeVideoPreview()" class="modal-close">✕</button>
                    </div>
                </div>
                <div class="modal-body">
                    <video controls class="preview-video" preload="metadata">
                        <source src="${videoUrl}" type="video/mp4">
                        <source src="${videoUrl}" type="video/x-matroska">
                        Your browser does not support the video tag.
                    </video>
                    <div class="preview-actions">
                        <button onclick="downloadFile('${jobId}', '${escapeHtml(filePath)}', '${escapeHtml(fileName)}')" 
                                class="btn btn-primary">📥 Download Video</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Remove existing preview
    const existingPreview = document.getElementById('video-preview-modal');
    if (existingPreview) {
        existingPreview.remove();
    }
    
    document.body.insertAdjacentHTML('beforeend', previewHtml);
    
    const modal = document.getElementById('video-preview-modal');
    modal.style.display = 'flex';
    
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeVideoPreview();
        }
    });
}

function previewAudio(jobId, filePath, fileName) {
    const token = getAuthToken();
    if (!token) {
        showError('Authentication required for audio preview');
        return;
    }
    
    // Create authenticated audio URL
    const audioUrl = `${API_BASE_URL}/jobs/${jobId}/files/${encodeURIComponent(filePath)}?token=${encodeURIComponent(token)}`;
    
    const previewHtml = `
        <div id="audio-preview-modal" class="modal">
            <div class="modal-content audio-preview-content">
                <div class="modal-header">
                    <h3 class="modal-title">🎵 ${fileName}</h3>
                    <div class="modal-controls">
                        <button onclick="closeAudioPreview()" class="modal-close">✕</button>
                    </div>
                </div>
                <div class="modal-body">
                    <audio controls class="preview-audio" preload="metadata">
                        <source src="${audioUrl}">
                        Your browser does not support the audio tag.
                    </audio>
                    <div class="preview-actions">
                        <button onclick="downloadFile('${jobId}', '${escapeHtml(filePath)}', '${escapeHtml(fileName)}')" 
                                class="btn btn-primary">📥 Download Audio</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Remove existing preview
    const existingPreview = document.getElementById('audio-preview-modal');
    if (existingPreview) {
        existingPreview.remove();
    }
    
    document.body.insertAdjacentHTML('beforeend', previewHtml);
    
    const modal = document.getElementById('audio-preview-modal');
    modal.style.display = 'flex';
    
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeAudioPreview();
        }
    });
}

function closeVideoPreview() {
    const modal = document.getElementById('video-preview-modal');
    if (modal) {
        modal.remove();
    }
}

function closeAudioPreview() {
    const modal = document.getElementById('audio-preview-modal');
    if (modal) {
        modal.remove();
    }
}

// Instrumental Selection Modal Functions
let currentReviewData = null;

async function showInstrumentalSelectionModal(jobId, correctedData) {
    try {
        currentJobId = jobId;
        
        showInfo('Loading instrumental options...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/corrections/${jobId}/instrumentals`);
        
        if (!response.ok) {
            throw new Error(`Failed to load instrumentals: ${response.status} ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Show the modal
        const modal = document.getElementById('instrumental-selection-modal');
        modal.style.display = 'flex';
        
        // Display the options with enhanced visualization
            displayInstrumentalOptions(data.instrumentals);
        
        // Store corrected data for later use
        window.currentCorrectedData = correctedData;
        
        showSuccess(`Found ${data.instrumentals.length} instrumental options`);
        
    } catch (error) {
        console.error('Error loading instrumental options:', error);
        showError(`Failed to load instrumental options: ${error.message}`);
    }
}

function displayInstrumentalOptions(instrumentals) {
    const container = document.getElementById('instrumental-selection-content');
    
    if (!instrumentals || instrumentals.length === 0) {
        container.innerHTML = '<div class="no-instrumentals">No instrumental files found for this job.</div>';
        return;
    }
    
    // Create visualization mode toggle
    const modeToggleHtml = `
        <div class="visualization-mode-toggle">
            <div class="mode-toggle-header">
                <h4>🎵 Visualization Mode</h4>
                <div class="mode-toggle-buttons">
                    <button id="waveform-mode-btn" class="mode-toggle-btn active" onclick="switchVisualizationMode('waveform')">
                        📊 Waveform
                    </button>
                    <button id="spectrogram-mode-btn" class="mode-toggle-btn" onclick="switchVisualizationMode('spectrogram')">
                        🌈 Spectrogram
                    </button>
                </div>
            </div>
            <div class="mode-description">
                <span id="mode-description-text">Waveform shows the audio amplitude over time - easier to see structure and timing</span>
            </div>
        </div>
    `;
    
    const optionsHtml = instrumentals.map(instrumental => createInstrumentalOptionHtml(instrumental)).join('');
    
    container.innerHTML = `
        ${modeToggleHtml}
        <div class="instrumental-options">
            ${optionsHtml}
        </div>
    `;
    
    // Initialize visualizations for all instrumentals
    setTimeout(() => {
        initializeAllVisualizations(instrumentals);
    }, 100);
}

function createInstrumentalOptionHtml(instrumental) {
    const recommendedBadge = instrumental.recommended ? '<span class="recommended-badge">⭐ RECOMMENDED</span>' : '';
    
    return `
        <div class="instrumental-option ${instrumental.recommended ? 'recommended' : ''}" 
             data-filename="${instrumental.filename}" 
             onclick="selectInstrumental('${instrumental.filename}', this)">
            
            ${recommendedBadge}
            
                <div class="instrumental-header">
                    <div class="instrumental-title">
                        <div class="instrumental-type">${instrumental.type}</div>
                    <div class="instrumental-filename">${instrumental.filename}</div>
                    </div>
                    <div class="instrumental-controls">
                        <div class="audio-preview-controls">
                        <audio class="audio-preview-player" 
                               data-filename="${instrumental.filename}"
                               controls preload="none"
                               ontimeupdate="updateVisualizationPlayhead('${instrumental.filename}', this.currentTime, this.duration)">
                            <!-- Audio source will be added after visualizations load -->
                            Your browser does not support the audio element.
                        </audio>
                        </div>
                    </div>
                </div>
            
                <div class="instrumental-description">${instrumental.description}</div>
            
            <!-- Main Instrumental Visualization -->
            <div class="visualization-container" data-filename="${instrumental.filename}">
                <div class="visualization-header">
                    <div class="visualization-title">
                        <span class="visualization-label">🎵 Main Track</span>
                        <span class="visualization-loading" id="viz-loading-${instrumental.filename.replace(/[^a-zA-Z0-9]/g, '_')}">Loading visualization...</span>
                    </div>
                </div>
                
                <div class="visualization-display" id="viz-display-${instrumental.filename.replace(/[^a-zA-Z0-9]/g, '_')}">
                    <div class="visualization-image-container">
                        <canvas class="visualization-canvas" id="viz-canvas-${instrumental.filename.replace(/[^a-zA-Z0-9]/g, '_')}" 
                                width="800" height="200"></canvas>
                        <div class="playhead-overlay" id="playhead-${instrumental.filename.replace(/[^a-zA-Z0-9]/g, '_')}">
                            <div class="playhead-line"></div>
                            <div class="playhead-time">0:00</div>
                        </div>
                        <div class="timeline-clicks" id="timeline-${instrumental.filename.replace(/[^a-zA-Z0-9]/g, '_')}" 
                             onclick="seekToPosition('${instrumental.filename}', event)"></div>
                    </div>
                </div>
            </div>
            
            <!-- Backing Vocals Visualization (if available) -->
            ${instrumental.backing_vocals_file ? createBackingVocalsVisualizationHtml(instrumental) : ''}
            
                <div class="instrumental-metadata">
                    <div class="instrumental-size">${instrumental.size_mb} MB</div>
                </div>
            </div>
        `;
}

function createBackingVocalsVisualizationHtml(instrumental) {
    const backingVocalsId = instrumental.backing_vocals_file.replace(/[^a-zA-Z0-9]/g, '_');
    
    return `
        <div class="backing-vocals-section">
            <div class="backing-vocals-header">
                <div class="backing-vocals-title">
                    <span class="visualization-label">🎤 Backing Vocals Track</span>
                    <span class="backing-vocals-info">(Used to create this instrumental)</span>
                </div>
                <div class="backing-vocals-controls">
                    <audio class="audio-preview-player backing-vocals-player" 
                           data-filename="${instrumental.backing_vocals_file}"
                           controls preload="none"
                           ontimeupdate="updateVisualizationPlayhead('${instrumental.backing_vocals_file}', this.currentTime, this.duration)">
                        <!-- Audio source will be added after visualizations load -->
                        Your browser does not support the audio element.
                    </audio>
                </div>
            </div>
            
            <div class="visualization-container backing-vocals-viz" data-filename="${instrumental.backing_vocals_file}">
                <div class="visualization-loading" id="bv-loading-${backingVocalsId}">Loading backing vocals visualization...</div>
                
                <div class="visualization-display" id="bv-display-${backingVocalsId}" style="display: none;">
                    <div class="visualization-image-container">
                        <canvas class="visualization-canvas" id="bv-canvas-${backingVocalsId}" 
                                width="800" height="150"></canvas>
                        <div class="playhead-overlay" id="bv-playhead-${backingVocalsId}">
                            <div class="playhead-line"></div>
                            <div class="playhead-time">0:00</div>
                        </div>
                        <div class="timeline-clicks" id="bv-timeline-${backingVocalsId}" 
                             onclick="seekToBackingVocalsPosition('${instrumental.backing_vocals_file}', event)"></div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

function switchVisualizationMode(mode) {
    currentVisualizationMode = mode;
    
    // Update toggle buttons
    document.getElementById('waveform-mode-btn').classList.toggle('active', mode === 'waveform');
    document.getElementById('spectrogram-mode-btn').classList.toggle('active', mode === 'spectrogram');
    
    // Update description
    const description = mode === 'waveform' 
        ? 'Waveform shows the audio amplitude over time - easier to see structure and timing'
        : 'Spectrogram shows frequency content over time - better for analyzing harmony and vocals';
    
    document.getElementById('mode-description-text').textContent = description;
    
    // Switch visualizations using cached data
    const instrumentalOptions = document.querySelectorAll('.instrumental-option');
    instrumentalOptions.forEach(async (option) => {
        const filename = option.dataset.filename;
        if (filename) {
            await switchCachedVisualization(filename, 'instrumental');
        }
        
        // Also switch backing vocals if present
        const backingVocalsSection = option.querySelector('.backing-vocals-section');
        if (backingVocalsSection) {
            const backingVocalsContainer = backingVocalsSection.querySelector('.visualization-container');
            const backingVocalsFilename = backingVocalsContainer.dataset.filename;
            if (backingVocalsFilename) {
                await switchCachedVisualization(backingVocalsFilename, 'backing_vocals');
            }
        }
    });
    
    showInfo(`Switched to ${mode} visualization mode`);
}

async function switchCachedVisualization(filename, fileType) {
    const cacheKey = `${filename}-${currentVisualizationMode}`;
    const cachedData = visualizationCache.get(cacheKey);
    
    if (cachedData !== undefined) {
        if (cachedData === null) {
            // This visualization failed to load previously
            const safeId = filename.replace(/[^a-zA-Z0-9]/g, '_');
            let loadingElement;
            
            if (fileType === 'backing_vocals') {
                loadingElement = document.getElementById(`bv-loading-${safeId}`);
            } else {
                loadingElement = document.getElementById(`viz-loading-${safeId}`);
            }
            
            if (loadingElement) {
                loadingElement.textContent = `⚠️ ${currentVisualizationMode} visualization unavailable`;
                loadingElement.style.color = '#f59e0b';
                loadingElement.style.display = 'block';
            }
        } else {
            // Use cached data
            renderVisualizationForFile(filename, fileType, cachedData);
        }
    } else {
        // Data not in cache, try to load it
        const safeId = filename.replace(/[^a-zA-Z0-9]/g, '_');
        let loadingElement;
        
        if (fileType === 'backing_vocals') {
            loadingElement = document.getElementById(`bv-loading-${safeId}`);
        } else {
            loadingElement = document.getElementById(`viz-loading-${safeId}`);
        }
        
        if (loadingElement) {
            loadingElement.style.display = 'block';
            loadingElement.textContent = `Loading ${currentVisualizationMode}...`;
            loadingElement.style.color = '';
        }
        
        try {
            // Attempt to load the visualization data
            await loadVisualizationData(filename, currentVisualizationMode);
            const newCachedData = visualizationCache.get(cacheKey);
            if (newCachedData && newCachedData !== null) {
                renderVisualizationForFile(filename, fileType, newCachedData);
            } else {
                // Loading failed and was cached as null
                if (loadingElement) {
                    loadingElement.textContent = `⚠️ ${currentVisualizationMode} visualization unavailable`;
                    loadingElement.style.color = '#f59e0b';
                }
            }
        } catch (error) {
            console.warn(`Error loading ${currentVisualizationMode} for ${filename}:`, error);
            if (loadingElement) {
                loadingElement.textContent = `⚠️ ${currentVisualizationMode} visualization unavailable`;
                loadingElement.style.color = '#f59e0b';
            }
        }
    }
}

async function initializeAllVisualizations(instrumentals) {
    try {
        // Use batch generation to avoid file locking issues
        await generateAllVisualizations(currentJobId);
        console.log('All visualizations loaded successfully');
        
    } catch (error) {
        console.error('Error loading visualizations:', error);
        showError('Some visualizations failed to load');
    }
}

async function generateAllVisualizations(jobId) {
    try {
        showInfo('Loading pre-generated visualizations...');
        
        // Visualizations are now pre-generated during Phase 2 processing
        // Just load them individually for each instrumental
        const instrumentalElements = document.querySelectorAll('.instrumental-option');
        
        const loadPromises = Array.from(instrumentalElements).map(async (element) => {
            const filename = element.dataset.filename;
            if (filename) {
                try {
                    // Load main instrumental visualization
                    await loadVisualizationForInstrumental(filename);
                    
                    // Check for backing vocals
                    const backingVocalsElement = element.querySelector('.backing-vocals-viz');
                    if (backingVocalsElement) {
                        const backingVocalsFilename = backingVocalsElement.dataset.filename;
                        if (backingVocalsFilename) {
                            await loadBackingVocalsVisualization(backingVocalsFilename);
                        }
                    }
                    return { filename, success: true };
                } catch (error) {
                    console.warn(`Failed to load visualizations for ${filename}:`, error);
                    return { filename, success: false, error };
                }
            }
            return { filename: 'unknown', success: false, error: 'No filename' };
        });
        
        // Use allSettled to allow some visualizations to fail without breaking everything
        const results = await Promise.allSettled(loadPromises);
        
        // Count successful and failed loads
        let successCount = 0;
        let failureCount = 0;
        
        results.forEach((result) => {
            if (result.status === 'fulfilled' && result.value.success) {
                successCount++;
            } else {
                failureCount++;
                if (result.status === 'fulfilled') {
                    console.warn(`Visualization failed for ${result.value.filename}:`, result.value.error);
                } else {
                    console.warn('Visualization promise rejected:', result.reason);
                }
            }
        });
        
        console.log(`Visualization loading complete: ${successCount} succeeded, ${failureCount} failed`);
        
        // Always enable audio previews regardless of visualization success
        enableAudioPreviews();
        
        hideInfo();
        
        // Show appropriate message based on results
        if (failureCount === 0) {
            showSuccess('All visualizations loaded successfully');
        } else if (successCount > 0) {
            showSuccess(`${successCount} visualizations loaded successfully. ${failureCount} failed to load but you can still preview audio and select tracks.`);
        } else {
            showInfo('Visualizations failed to load, but you can still preview audio and select tracks.');
        }
        
    } catch (error) {
        console.error('Error in generateAllVisualizations:', error);
        hideInfo();
        
        // Always try to enable audio previews even if visualization loading had errors
        try {
            enableAudioPreviews();
            showInfo('Visualizations failed to load, but audio previews are available for track selection.');
        } catch (audioError) {
            console.error('Error enabling audio previews:', audioError);
            showError('Failed to load visualizations and audio previews. You can still select tracks manually.');
        }
    }
}

function renderVisualizationForFile(filename, fileType, visualizationData) {
    const safeId = filename.replace(/[^a-zA-Z0-9]/g, '_');
    
    let canvasElement, loadingElement, displayElement;
    
    if (fileType === 'backing_vocals') {
        // Backing vocals visualization
        canvasElement = document.getElementById(`bv-canvas-${safeId}`);
        loadingElement = document.getElementById(`bv-loading-${safeId}`);
        displayElement = document.getElementById(`bv-display-${safeId}`);
    } else {
        // Main instrumental visualization
        canvasElement = document.getElementById(`viz-canvas-${safeId}`);
        loadingElement = document.getElementById(`viz-loading-${safeId}`);
        displayElement = document.getElementById(`viz-display-${safeId}`);
    }
    
    if (!canvasElement) {
        console.warn(`Canvas not found for ${filename} (${fileType})`);
        return;
    }
    
    try {
        // Render to canvas
        renderVisualizationToCanvas(canvasElement, visualizationData);
        
        // Hide loading, show display
        if (loadingElement) loadingElement.style.display = 'none';
        if (displayElement) displayElement.style.display = 'block';
        
    } catch (error) {
        console.error(`Error rendering visualization for ${filename}:`, error);
        
        if (loadingElement) {
            loadingElement.textContent = `Failed to load visualization`;
            loadingElement.style.color = '#ef4444';
        }
    }
}

function enableAudioPreviews() {
    console.log('🔊 Enabling audio previews...');
    
    let instrumentalCount = 0;
    let backingVocalsCount = 0;
    let errorCount = 0;
    
    try {
        showInfo('Setting up audio previews...', 1500);
        
        // Enable main instrumental audio previews
        const instrumentalAudioElements = document.querySelectorAll('.audio-preview-player:not(.backing-vocals-player)');
        console.log(`Found ${instrumentalAudioElements.length} instrumental audio elements`);
        
        instrumentalAudioElements.forEach(audio => {
            try {
                const filename = audio.dataset.filename;
                if (filename && !audio.querySelector('source')) {
                    const source = document.createElement('source');
                    source.src = `${API_BASE_URL}/corrections/${currentJobId}/instrumental-preview/${filename}`;
                    source.type = 'audio/flac';
                    audio.appendChild(source);
                    
                    // Load the audio now that the source is set
                    audio.load();
                    
                    instrumentalCount++;
                    console.log(`✅ Enabled audio preview for: ${filename}`);
                } else if (!filename) {
                    console.warn('Audio element missing filename:', audio);
                } else {
                    console.log(`Audio preview already enabled for: ${filename}`);
                }
            } catch (audioError) {
                errorCount++;
                console.warn('Error enabling individual audio preview:', audioError);
            }
        });
        
        // Enable backing vocals audio previews
        const backingVocalsAudioElements = document.querySelectorAll('.backing-vocals-player');
        console.log(`Found ${backingVocalsAudioElements.length} backing vocals audio elements`);
        
        backingVocalsAudioElements.forEach(audio => {
            try {
                const filename = audio.dataset.filename;
                if (filename && !audio.querySelector('source')) {
                    const source = document.createElement('source');
                    source.src = `${API_BASE_URL}/corrections/${currentJobId}/backing-vocals-preview/${filename}`;
                    source.type = 'audio/flac';
                    audio.appendChild(source);
                    
                    // Load the audio now that the source is set
                    audio.load();
                    
                    backingVocalsCount++;
                    console.log(`✅ Enabled backing vocals audio preview for: ${filename}`);
                } else if (!filename) {
                    console.warn('Backing vocals element missing filename:', audio);
                } else {
                    console.log(`Backing vocals preview already enabled for: ${filename}`);
                }
            } catch (audioError) {
                errorCount++;
                console.warn('Error enabling backing vocals audio preview:', audioError);
            }
        });
        
        console.log(`🎵 Audio preview setup complete: ${instrumentalCount} instrumental, ${backingVocalsCount} backing vocals, ${errorCount} errors`);
        
        if (instrumentalCount > 0 || backingVocalsCount > 0) {
            showInfo(`Audio previews ready - you can now play and compare tracks (${instrumentalCount + backingVocalsCount} tracks available)`, 2000);
        } else {
            showInfo('Audio previews setup complete - tracks should be ready shortly', 1500);
        }
        
        // Always ensure the instrumental selection functionality is working
        // even if audio previews had issues
        ensureInstrumentalSelectionWorks();
        
    } catch (error) {
        console.error('Error in enableAudioPreviews:', error);
        errorCount++;
        
        // Still try to ensure basic functionality works
        try {
            ensureInstrumentalSelectionWorks();
            showInfo('Some audio previews may not be available, but you can still select tracks to proceed');
        } catch (fallbackError) {
            console.error('Error in fallback setup:', fallbackError);
            showError('Audio previews unavailable, but track selection should still work');
        }
    }
}

function ensureInstrumentalSelectionWorks() {
    // Make sure all instrumental options are clickable and the confirm button is properly set up
    const instrumentalOptions = document.querySelectorAll('.instrumental-option');
    console.log(`🎯 Ensuring ${instrumentalOptions.length} instrumental options are selectable`);
    
    instrumentalOptions.forEach((option, index) => {
        const filename = option.dataset.filename;
        if (filename) {
            // Ensure the click handler works by adding a backup event listener
            option.addEventListener('click', function(e) {
                console.log(`📍 Backup click handler for ${filename}`);
                selectInstrumental(filename, this);
            });
            
            // Make sure the option is visually interactive
            option.style.cursor = 'pointer';
            option.style.userSelect = 'none';
            
            console.log(`✅ Instrumental option ${index + 1} (${filename}) is ready for selection`);
        }
    });
    
    // Also ensure all timeline click handlers are working
    ensureTimelineClickHandlers();
    
    // Update the confirm button state
    updateConfirmInstrumentalButton();
    
    console.log('🎯 Instrumental selection functionality verified');
}

function ensureTimelineClickHandlers() {
    console.log('🎯 Verifying timeline click handlers...');
    
    // Check main instrumental timeline click handlers
    const timelineElements = document.querySelectorAll('.timeline-clicks');
    console.log(`Found ${timelineElements.length} timeline click elements`);
    
    timelineElements.forEach((timeline, index) => {
        const timelineId = timeline.id;
        console.log(`📍 Checking timeline element ${index + 1}: ${timelineId}`);
        
        // Make sure it's visible and clickable
        timeline.style.cursor = 'pointer';
        timeline.style.pointerEvents = 'auto';
        
        // Check if it has a click handler already
        const hasOnClick = timeline.getAttribute('onclick');
        if (!hasOnClick) {
            console.warn(`⚠️ Timeline element ${timelineId} missing onclick handler`);
            
            // Try to determine the filename from the ID and add a backup handler
            if (timelineId.startsWith('timeline-')) {
                const filenameId = timelineId.replace('timeline-', '');
                // Try to find the corresponding instrumental option to get the real filename
                const instrumentalOption = document.querySelector(`[data-filename*="${filenameId}"]`);
                if (instrumentalOption) {
                    const filename = instrumentalOption.dataset.filename;
                    console.log(`📍 Adding backup click handler for ${filename}`);
                    timeline.addEventListener('click', function(e) {
                        console.log(`📍 Backup timeline click for ${filename}`);
                        seekToPosition(filename, e);
                    });
                }
            } else if (timelineId.startsWith('bv-timeline-')) {
                const filenameId = timelineId.replace('bv-timeline-', '');
                // Try to find the corresponding backing vocals element
                const backingVocalsElement = document.querySelector(`[data-filename*="${filenameId}"]`);
                if (backingVocalsElement) {
                    const filename = backingVocalsElement.dataset.filename;
                    console.log(`📍 Adding backup click handler for backing vocals ${filename}`);
                    timeline.addEventListener('click', function(e) {
                        console.log(`📍 Backup backing vocals timeline click for ${filename}`);
                        seekToBackingVocalsPosition(filename, e);
                    });
                }
            }
        } else {
            console.log(`✅ Timeline element ${timelineId} has click handler: ${hasOnClick.substring(0, 50)}...`);
        }
        
        // Ensure it's visible
        if (timeline.style.display === 'none') {
            console.warn(`⚠️ Timeline element ${timelineId} is hidden - making visible`);
            timeline.style.display = 'block';
        }
    });
    
    console.log('🎯 Timeline click handlers verification complete');
}

async function loadVisualizationForInstrumental(filename) {
    const safeId = filename.replace(/[^a-zA-Z0-9]/g, '_');
    const loadingElement = document.getElementById(`viz-loading-${safeId}`);
    const displayElement = document.getElementById(`viz-display-${safeId}`);
    const canvasElement = document.getElementById(`viz-canvas-${safeId}`);
    
    if (!canvasElement) {
        console.warn(`Canvas not found for ${filename}`);
        return;
    }
    
    try {
        // Load both waveform and spectrogram data to cache for smooth switching
        const results = await Promise.allSettled([
            loadVisualizationData(filename, 'waveform'),
            loadVisualizationData(filename, 'spectrogram')
        ]);
        
        // Check if at least one visualization type loaded successfully
        let hasAnyVisualization = false;
        results.forEach((result, index) => {
            const vizType = index === 0 ? 'waveform' : 'spectrogram';
            if (result.status === 'fulfilled') {
                const cacheKey = `${filename}-${vizType}`;
                if (visualizationCache.has(cacheKey)) {
                    hasAnyVisualization = true;
                }
            } else {
                console.warn(`Failed to load ${vizType} for ${filename}:`, result.reason);
            }
        });
        
        if (hasAnyVisualization) {
            // Display the current visualization mode if available
            const cacheKey = `${filename}-${currentVisualizationMode}`;
            if (visualizationCache.has(cacheKey)) {
                const cachedData = visualizationCache.get(cacheKey);
                renderVisualizationToCanvas(canvasElement, cachedData);
                
                if (loadingElement) loadingElement.style.display = 'none';
                if (displayElement) displayElement.style.display = 'block';
            } else {
                // Try the other visualization mode
                const alternateMode = currentVisualizationMode === 'waveform' ? 'spectrogram' : 'waveform';
                const alternateCacheKey = `${filename}-${alternateMode}`;
                if (visualizationCache.has(alternateCacheKey)) {
                    const cachedData = visualizationCache.get(alternateCacheKey);
                    renderVisualizationToCanvas(canvasElement, cachedData);
                    
                    if (loadingElement) {
                        loadingElement.textContent = `${currentVisualizationMode} unavailable, showing ${alternateMode}`;
                        loadingElement.style.color = '#f59e0b';
                        loadingElement.style.display = 'block';
                    }
                    if (displayElement) displayElement.style.display = 'block';
                } else {
                    throw new Error('No visualizations available');
                }
            }
        } else {
            throw new Error('All visualization types failed to load');
        }
        
    } catch (error) {
        console.warn(`Error loading visualizations for ${filename}:`, error);
        
        // Show error state but keep everything functional
        if (loadingElement) {
            loadingElement.textContent = `⚠️ Visualization unavailable (audio preview still works)`;
            loadingElement.style.color = '#f59e0b';
            loadingElement.style.display = 'block';
        }
        
        // Hide the canvas but keep the container and click areas visible
        if (canvasElement) {
            canvasElement.style.display = 'none';
        }
        
        // Make sure the display element is shown so the audio controls remain accessible
        if (displayElement) {
            displayElement.style.display = 'block';
        }
        
        // Ensure the timeline click handler is still available by creating a fallback click area
        const timelineClicksElement = document.getElementById(`timeline-${filename.replace(/[^a-zA-Z0-9]/g, '_')}`);
        if (timelineClicksElement) {
            // Make sure the timeline clicks element is visible and clickable even without visualization
            timelineClicksElement.style.display = 'block';
            timelineClicksElement.style.height = '40px';
            timelineClicksElement.style.backgroundColor = '#f3f4f6';
            timelineClicksElement.style.border = '1px dashed #d1d5db';
            timelineClicksElement.style.borderRadius = '4px';
            timelineClicksElement.style.cursor = 'pointer';
            timelineClicksElement.style.position = 'relative';
            timelineClicksElement.innerHTML = '<div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #6b7280; font-size: 12px;">Click to seek in audio</div>';
        }
        
        // Don't throw the error - let the modal continue functioning
    }
}

async function loadVisualizationData(filename, visualizationType) {
    const cacheKey = `${filename}-${visualizationType}`;
    
    // Skip if already cached
    if (visualizationCache.has(cacheKey)) {
        return;
    }
    
    try {
        // Use unified endpoint for all files
        const endpoint = `${API_BASE_URL}/corrections/${currentJobId}/visualization/${visualizationType}/${filename}`;
        
        const response = await authenticatedFetch(endpoint);
        
        if (!response) {
            // Auth failed, already handled by authenticatedFetch
            throw new Error('Authentication failed');
        }
        
        if (!response.ok) {
            if (response.status === 404) {
                throw new Error(`${visualizationType} visualization not found (404) - this is normal if visualization wasn't generated`);
            } else {
                throw new Error(`Failed to load ${visualizationType}: HTTP ${response.status}`);
            }
        }
        
        const data = await response.json();
        
        // Validate the response data
        if (!data || (!data.waveform_data && !data.spectrogram_data)) {
            throw new Error(`Invalid ${visualizationType} data received - missing visualization data`);
        }
        
        // Cache the data
        visualizationCache.set(cacheKey, data);
        const fileType = filename.includes('Backing Vocals') ? 'backing vocals' : 'instrumental';
        console.log(`✅ Cached ${visualizationType} data for ${filename} (${fileType})`);
        
    } catch (error) {
        console.warn(`⚠️ Could not load ${visualizationType} for ${filename}:`, error.message);
        // Mark in cache that this visualization failed to load to avoid repeated requests
        visualizationCache.set(cacheKey, null);
        // Re-throw the error so calling functions can handle it appropriately
        throw error;
    }
}

async function loadBackingVocalsVisualization(backingVocalsFilename) {
    const safeId = backingVocalsFilename.replace(/[^a-zA-Z0-9]/g, '_');
    const loadingElement = document.getElementById(`bv-loading-${safeId}`);
    const displayElement = document.getElementById(`bv-display-${safeId}`);
    const canvasElement = document.getElementById(`bv-canvas-${safeId}`);
    
    if (!canvasElement) {
        console.warn(`Backing vocals canvas not found for ${backingVocalsFilename}`);
        return;
    }
    
    try {
        // Load both waveform and spectrogram data to cache for smooth switching
        const results = await Promise.allSettled([
            loadVisualizationData(backingVocalsFilename, 'waveform'),
            loadVisualizationData(backingVocalsFilename, 'spectrogram')
        ]);
        
        // Check if at least one visualization type loaded successfully
        let hasAnyVisualization = false;
        results.forEach((result, index) => {
            const vizType = index === 0 ? 'waveform' : 'spectrogram';
            if (result.status === 'fulfilled') {
                const cacheKey = `${backingVocalsFilename}-${vizType}`;
                if (visualizationCache.has(cacheKey)) {
                    hasAnyVisualization = true;
                }
            } else {
                console.warn(`Failed to load backing vocals ${vizType} for ${backingVocalsFilename}:`, result.reason);
            }
        });
        
        if (hasAnyVisualization) {
            // Display the current visualization mode if available
            const cacheKey = `${backingVocalsFilename}-${currentVisualizationMode}`;
            if (visualizationCache.has(cacheKey)) {
                const cachedData = visualizationCache.get(cacheKey);
                renderVisualizationToCanvas(canvasElement, cachedData);
                
                if (loadingElement) loadingElement.style.display = 'none';
                if (displayElement) displayElement.style.display = 'block';
            } else {
                // Try the other visualization mode
                const alternateMode = currentVisualizationMode === 'waveform' ? 'spectrogram' : 'waveform';
                const alternateCacheKey = `${backingVocalsFilename}-${alternateMode}`;
                if (visualizationCache.has(alternateCacheKey)) {
                    const cachedData = visualizationCache.get(alternateCacheKey);
                    renderVisualizationToCanvas(canvasElement, cachedData);
                    
                    if (loadingElement) {
                        loadingElement.textContent = `${currentVisualizationMode} unavailable, showing ${alternateMode}`;
                        loadingElement.style.color = '#f59e0b';
                        loadingElement.style.display = 'block';
                    }
                    if (displayElement) displayElement.style.display = 'block';
                } else {
                    throw new Error('No backing vocals visualizations available');
                }
            }
        } else {
            throw new Error('All backing vocals visualization types failed to load');
        }
        
    } catch (error) {
        console.warn(`Error loading backing vocals visualizations for ${backingVocalsFilename}:`, error);
        
        // Show error state but keep everything functional
        if (loadingElement) {
            loadingElement.textContent = '⚠️ Backing vocals visualization unavailable (audio preview still works)';
            loadingElement.style.color = '#f59e0b';
            loadingElement.style.display = 'block';
        }
        
        // Hide the canvas but keep the container and click areas visible
        if (canvasElement) {
            canvasElement.style.display = 'none';
        }
        
        // Make sure the display element is shown so the audio controls remain accessible
        if (displayElement) {
            displayElement.style.display = 'block';
        }
        
        // Ensure the backing vocals timeline click handler is still available
        const backingVocalsId = backingVocalsFilename.replace(/[^a-zA-Z0-9]/g, '_');
        const timelineClicksElement = document.getElementById(`bv-timeline-${backingVocalsId}`);
        if (timelineClicksElement) {
            // Make sure the timeline clicks element is visible and clickable even without visualization
            timelineClicksElement.style.display = 'block';
            timelineClicksElement.style.height = '30px';
            timelineClicksElement.style.backgroundColor = '#f3f4f6';
            timelineClicksElement.style.border = '1px dashed #d1d5db';
            timelineClicksElement.style.borderRadius = '4px';
            timelineClicksElement.style.cursor = 'pointer';
            timelineClicksElement.style.position = 'relative';
            timelineClicksElement.innerHTML = '<div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #6b7280; font-size: 11px;">Click to seek in backing vocals</div>';
        }
        
        // Don't throw the error - let the modal continue functioning
    }
}

function renderVisualizationToCanvas(canvas, data) {
    if (!canvas || !data || !data.waveform_data && !data.spectrogram_data) {
        console.warn('Invalid canvas or data for rendering');
        return;
    }
    
    const ctx = canvas.getContext('2d');
    
    // Create image from base64 data
    const img = new Image();
    img.onload = function() {
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Draw the visualization image
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        
        // Store duration for playhead calculations
        canvas.dataset.duration = data.duration || 0;
    };
    
    // Use appropriate data based on visualization mode
    const imageData = data.waveform_data || data.spectrogram_data;
    img.src = `data:image/png;base64,${imageData}`;
}

function updateVisualizationPlayhead(filename, currentTime, duration) {
    // Update playhead for main instrumental
    const safeId = filename.replace(/[^a-zA-Z0-9]/g, '_');
    
    // Try both main instrumental and backing vocals playhead elements
    const playheadElement = document.getElementById(`playhead-${safeId}`) || 
                          document.getElementById(`bv-playhead-${safeId}`);
    
    if (!playheadElement || !duration || duration === 0) {
        return;
    }
    
    // Calculate position as percentage
    const position = (currentTime / duration) * 100;
    
    // Update playhead line position
    const playheadLine = playheadElement.querySelector('.playhead-line');
    if (playheadLine) {
        playheadLine.style.left = `${position}%`;
    }
    
    // Update time display
    const playheadTime = playheadElement.querySelector('.playhead-time');
    if (playheadTime) {
        const minutes = Math.floor(currentTime / 60);
        const seconds = Math.floor(currentTime % 60);
        playheadTime.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
        playheadTime.style.left = `${Math.min(position, 90)}%`; // Keep text visible
    }
}

function seekToPosition(filename, event) {
    console.log(`🎯 seekToPosition called for: ${filename}`);
    
    // Prevent event bubbling to avoid triggering selectInstrumental
    event.stopPropagation();
    event.preventDefault();
    
    try {
        const timelineElement = event.currentTarget;
        const rect = timelineElement.getBoundingClientRect();
        const clickX = event.clientX - rect.left;
        
        // Calculate percentage with improved accuracy
        // The backend now generates images with minimal left padding for precise seeking
        let percentage = clickX / rect.width;
        
        // Ensure percentage is within bounds
        percentage = Math.max(0, Math.min(1, percentage));
        
        console.log(`🎯 Calculated seek percentage: ${(percentage * 100).toFixed(1)}%`);
        
        // Find the corresponding audio player
        const audioPlayer = document.querySelector(`audio[data-filename="${filename}"]`);
        if (audioPlayer) {
            if (audioPlayer.duration) {
                const newTime = percentage * audioPlayer.duration;
                audioPlayer.currentTime = newTime;
                
                console.log(`🎯 Seeking to ${newTime.toFixed(1)}s of ${audioPlayer.duration.toFixed(1)}s`);
                
                // Start playback automatically
                audioPlayer.play().then(() => {
                    console.log(`✅ Playback started successfully for ${filename}`);
                }).catch(error => {
                    console.warn('Could not start playback:', error);
                    // Still update playhead even if playback fails
                    updateVisualizationPlayhead(filename, newTime, audioPlayer.duration);
                });
            } else {
                console.warn(`Audio for ${filename} is not ready for seeking yet (duration: ${audioPlayer.duration})`);
                showInfo('Loading audio - please try again in a moment');
                
                // Try to load the audio and then seek
                audioPlayer.addEventListener('loadedmetadata', () => {
                    if (audioPlayer.duration) {
                        const newTime = percentage * audioPlayer.duration;
                        audioPlayer.currentTime = newTime;
                        console.log(`🎯 Delayed seek to ${newTime.toFixed(1)}s after metadata loaded`);
                        audioPlayer.play().catch(error => {
                            console.warn('Could not start playback after loading metadata:', error);
                        });
                    }
                }, { once: true });
                
                // Force load if not already loading
                audioPlayer.load();
            }
        } else {
            console.warn(`Audio player not found for ${filename} - seeking not available`);
            showInfo('Audio preview not ready yet - please wait a moment and try again');
        }
    } catch (error) {
        console.error('Error in seekToPosition:', error);
        showError('Error seeking in audio - please try again');
    }
}

function seekToBackingVocalsPosition(backingVocalsFilename, event) {
    console.log(`🎯 seekToBackingVocalsPosition called for: ${backingVocalsFilename}`);
    
    // Prevent event bubbling to avoid triggering selectInstrumental
    event.stopPropagation();
    event.preventDefault();
    
    try {
        const timelineElement = event.currentTarget;
        const rect = timelineElement.getBoundingClientRect();
        const clickX = event.clientX - rect.left;
        
        // Calculate percentage with improved accuracy
        // The backend now generates images with minimal left padding for precise seeking
        let percentage = clickX / rect.width;
        
        // Ensure percentage is within bounds
        percentage = Math.max(0, Math.min(1, percentage));
        
        console.log(`🎯 Calculated backing vocals seek percentage: ${(percentage * 100).toFixed(1)}%`);
        
        // Find the corresponding backing vocals audio player
        const audioPlayer = document.querySelector(`audio[data-filename="${backingVocalsFilename}"]`);
        if (audioPlayer) {
            if (audioPlayer.duration) {
                const newTime = percentage * audioPlayer.duration;
                audioPlayer.currentTime = newTime;
                
                console.log(`🎯 Seeking backing vocals to ${newTime.toFixed(1)}s of ${audioPlayer.duration.toFixed(1)}s`);
                
                // Start playback automatically
                audioPlayer.play().then(() => {
                    console.log(`✅ Backing vocals playback started successfully for ${backingVocalsFilename}`);
                }).catch(error => {
                    console.warn('Could not start backing vocals playback:', error);
                    // Still update playhead even if playback fails
                    updateVisualizationPlayhead(backingVocalsFilename, newTime, audioPlayer.duration);
                });
            } else {
                console.warn(`Backing vocals audio for ${backingVocalsFilename} is not ready for seeking yet (duration: ${audioPlayer.duration})`);
                showInfo('Loading backing vocals audio - please try again in a moment');
                
                // Try to load the audio and then seek
                audioPlayer.addEventListener('loadedmetadata', () => {
                    if (audioPlayer.duration) {
                        const newTime = percentage * audioPlayer.duration;
                        audioPlayer.currentTime = newTime;
                        console.log(`🎯 Delayed backing vocals seek to ${newTime.toFixed(1)}s after metadata loaded`);
                        audioPlayer.play().catch(error => {
                            console.warn('Could not start backing vocals playback after loading metadata:', error);
                        });
                    }
                }, { once: true });
                
                // Force load if not already loading
                audioPlayer.load();
            }
        } else {
            console.warn(`Backing vocals audio player not found for ${backingVocalsFilename} - seeking not available`);
            showInfo('Backing vocals preview not ready yet - please wait a moment and try again');
        }
    } catch (error) {
        console.error('Error in seekToBackingVocalsPosition:', error);
        showError('Error seeking in backing vocals audio - please try again');
    }
}

function selectInstrumental(filename, element) {
    console.log('🎵 selectInstrumental called with:', filename);
    
    try {
        // Remove selection from all options
        document.querySelectorAll('.instrumental-option').forEach(opt => {
            opt.classList.remove('selected');
        });
        
        // Add selection to clicked option
        if (element) {
            element.classList.add('selected');
        } else {
            console.warn('Element not provided to selectInstrumental, finding by filename');
            const targetElement = document.querySelector(`[data-filename="${filename}"]`);
            if (targetElement) {
                targetElement.classList.add('selected');
            }
        }
        
        selectedInstrumental = filename;
        console.log('✅ Selected instrumental:', selectedInstrumental);
        
        // Update confirm button
        updateConfirmInstrumentalButton();
        
        // Pause any currently playing audio
        document.querySelectorAll('.audio-preview-player').forEach(audio => {
            try {
                if (!audio.paused) {
                    audio.pause();
                }
            } catch (audioError) {
                console.warn('Error pausing audio:', audioError);
            }
        });
        
        showInfo(`Selected: ${filename}`);
        
    } catch (error) {
        console.error('Error in selectInstrumental:', error);
        showError('Error selecting instrumental - please try again');
    }
}

// ... existing code ...

function updateConfirmInstrumentalButton() {
    const confirmBtn = document.getElementById('confirm-instrumental-btn');
    if (confirmBtn) {
        confirmBtn.disabled = !selectedInstrumental;
        confirmBtn.textContent = `🎵 Continue with Selected Track`;
    }
}

async function confirmInstrumentalSelection() {
    if (!selectedInstrumental) {
        showError('Please select an instrumental track first');
        return;
    }
    
    // Hide the instrumental selection modal without resetting state
    hideInstrumentalSelectionModal();
    
    // Show YouTube upload confirmation modal
    showYouTubeUploadConfirmationModal();
}

async function showYouTubeUploadConfirmationModal() {
    try {
        // Check YouTube authentication status
        const authStatus = await checkYouTubeAuthStatus();
        
        const modalHtml = `
            <div id="youtube-upload-modal" class="modal">
                <div class="modal-content youtube-upload-modal-content">
                    <div class="modal-header">
                        <h3 class="modal-title">📺 YouTube Upload (Optional)</h3>
                        <div class="modal-controls">
                            <button onclick="closeYouTubeUploadModal()" class="modal-close">✕</button>
                        </div>
                    </div>
                    <div class="modal-body">
                        <div class="youtube-upload-content">
                            <div class="upload-intro">
                                <p>Would you like to automatically upload your completed karaoke video to YouTube?</p>
                            </div>
                            
                            <div class="youtube-auth-section">
                                <div id="youtube-auth-status-confirm" class="youtube-auth-status">
                                    ${createYouTubeAuthStatusHtml(authStatus)}
                                </div>
                            </div>
                            
                            <div class="upload-actions">
                                <button onclick="completeFinalizationWithYouTube(true)" class="btn btn-primary" ${!authStatus.authenticated ? 'disabled' : ''}>
                                    📺 Complete & Upload to YouTube
                                </button>
                                <button onclick="completeFinalizationWithYouTube(false)" class="btn btn-secondary">
                                    🎵 Complete Without YouTube Upload
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Remove any existing modal
        const existingModal = document.getElementById('youtube-upload-modal');
        if (existingModal) {
            existingModal.remove();
        }
        
        // Add modal to body
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        // Show modal
        const modal = document.getElementById('youtube-upload-modal');
        modal.style.display = 'flex';
        
        // Add click outside to close
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                closeYouTubeUploadModal();
            }
        });
        
    } catch (error) {
        console.error('Error showing YouTube upload confirmation:', error);
        // If there's an error, just proceed without YouTube upload
        completeFinalizationWithYouTube(false);
    }
}

function createYouTubeAuthStatusHtml(authStatus) {
    if (authStatus.authenticated) {
        return `
            <div class="youtube-auth-info authenticated">
                ✅ Authenticated with YouTube
                <div class="auth-details">
                    You can upload videos directly to your YouTube channel
                </div>
            </div>
            <div class="youtube-auth-actions">
                <button onclick="revokeYouTubeAuthAndUpdate()" class="btn btn-link">
                    Revoke Authentication
                </button>
            </div>
        `;
    } else {
        return `
            <div class="youtube-auth-info not-authenticated">
                ⚠️ Not authenticated with YouTube
                <div class="auth-details">
                    Videos will be generated but not uploaded automatically
                </div>
            </div>
            <div class="youtube-auth-actions">
                <button onclick="authenticateYouTubeAndUpdateConfirm()" class="btn btn-primary">
                    🔑 Authenticate with YouTube
                </button>
            </div>
        `;
    }
}

async function completeFinalizationWithYouTube(uploadToYoutube) {
    try {
        // Get the current job ID from the stored value
        const jobId = window.currentInstrumentalJobId || getCurrentJobIdFromUrl();
        
        if (!jobId) {
            showError('Unable to determine job ID. Please refresh and try again.');
            return;
        }
        
        // Determine the current job status to know which endpoint to call
        const jobResponse = await authenticatedFetch(`${API_BASE_URL}/jobs/${jobId}`);
        if (!jobResponse) return; // Auth failed, already handled
        
        const jobData = await jobResponse.json();
        const jobStatus = jobData.status;
        
        let endpoint, requestData, infoMessage, successMessage;
        
        if (jobStatus === 'reviewing') {
            // For reviewing status: complete the review with instrumental selection
            endpoint = `/corrections/${jobId}/complete`;
            requestData = {
                corrected_data: currentReviewData || {},
                selected_instrumental: selectedInstrumental,
                upload_to_youtube: uploadToYoutube
            };
            infoMessage = uploadToYoutube ? 
                'Completing review and preparing for YouTube upload...' :
                'Completing review with selected instrumental...';
            successMessage = uploadToYoutube ?
                'Review completed - video will be uploaded to YouTube when ready' :
                'Review completed with instrumental selection';
        } else if (jobStatus === 'ready_for_finalization') {
            // For ready_for_finalization status: finalize with instrumental selection
            endpoint = `/corrections/${jobId}/finalize`;
            requestData = {
                selected_instrumental: selectedInstrumental,
                upload_to_youtube: uploadToYoutube
            };
            infoMessage = uploadToYoutube ?
                'Finalizing and preparing for YouTube upload...' :
                'Finalizing with selected instrumental...';
            successMessage = uploadToYoutube ?
                'Finalization started - video will be uploaded to YouTube when ready' :
                'Finalization started with selected instrumental';
        } else {
            showError(`Cannot select instrumental for job in status: ${jobStatus}`);
            return;
        }
        
        showInfo(infoMessage);
        
        // Close the YouTube upload modal
        closeYouTubeUploadModal();
        
        // Send request to the appropriate endpoint
        const response = await authenticatedFetch(`${API_BASE_URL}${endpoint}`, {
            method: 'POST',
            body: JSON.stringify(requestData)
        });
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const result = await response.json();
            showSuccess(result.message || successMessage);
            
            // Refresh jobs to show updated status
            await loadJobs();
            
            // Reset state after successful completion
            selectedInstrumental = null;
            currentReviewData = null;
            window.currentInstrumentalJobId = null;
        } else {
            const error = await response.json();
            showError('Error processing request: ' + error.detail);
        }
        
    } catch (error) {
        console.error('Error completing finalization:', error);
        showError('Error processing request: ' + error.message);
    }
}

function closeYouTubeUploadModal() {
    const modal = document.getElementById('youtube-upload-modal');
    if (modal) {
        modal.remove();
    }
    
    // Reset state when YouTube upload modal is closed
    selectedInstrumental = null;
    currentReviewData = null;
    window.currentInstrumentalJobId = null;
}

async function authenticateYouTubeAndUpdateConfirm() {
    const authBtn = document.querySelector('#youtube-upload-modal .btn-primary');
    const originalText = authBtn ? authBtn.textContent : '';
    
    if (authBtn) {
        authBtn.disabled = true;
        authBtn.textContent = '🔄 Authenticating...';
    }
    
    try {
        const success = await authenticateWithYouTube();
        
        if (success) {
            // Refresh auth status in the confirmation modal
            const newStatus = await checkYouTubeAuthStatus();
            const authStatusContainer = document.getElementById('youtube-auth-status-confirm');
            if (authStatusContainer) {
                authStatusContainer.innerHTML = createYouTubeAuthStatusHtml(newStatus);
            }
            
            // Enable the YouTube upload button
            const uploadBtn = document.querySelector('#youtube-upload-modal button[onclick="completeFinalizationWithYouTube(true)"]');
            if (uploadBtn) {
                uploadBtn.disabled = false;
            }
        }
    } finally {
        // Reset button if it still exists
        if (authBtn) {
            authBtn.disabled = false;
            authBtn.textContent = originalText;
        }
    }
}

function hideInstrumentalSelectionModal() {
    const modal = document.getElementById('instrumental-selection-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    // Note: We don't reset state here - this is used when transitioning between modals
}

function closeInstrumentalSelectionModal() {
    const modal = document.getElementById('instrumental-selection-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    
    // Reset state
    selectedInstrumental = null;
    currentReviewData = null;
    window.currentInstrumentalJobId = null;
    
    // Clear modal content
    const content = document.getElementById('instrumental-selection-content');
    if (content) {
        content.innerHTML = '<div class="instrumental-loading">Loading instrumental options...</div>';
    }
    
    // Reset confirmation button
    const confirmBtn = document.getElementById('confirm-instrumental-btn');
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = '✅ Use Selected Instrumental & Complete';
    }
}

function getCurrentJobIdFromUrl() {
    // This is a placeholder - you may need to implement this based on how job ID is available in your context
    // For now, we'll look for it in various places
    
    // Try to get from modal title
    const modalJobId = document.getElementById('modal-job-id');
    if (modalJobId && modalJobId.textContent) {
        return modalJobId.textContent.trim();
    }
    
    // Try to get from URL params if it's there
    const urlParams = new URLSearchParams(window.location.search);
    const jobId = urlParams.get('job_id');
    if (jobId) {
        return jobId;
    }
    
    // Try to get from the most recent job in the list that's in review state
    const jobs = Array.from(document.querySelectorAll('.job[data-job-id]'));
    for (const jobElement of jobs) {
        const statusBadge = jobElement.querySelector('.status-badge');
        if (statusBadge && statusBadge.textContent.toLowerCase().includes('review')) {
            return jobElement.getAttribute('data-job-id');
        }
    }
    
    return null;
}

async function showInstrumentalSelectionForJob(jobId) {
    try {
        showInfo('Loading instrumental options for job...');
        
        // For jobs in "reviewing" state, we assume lyrics review is complete
        // and we just need to select an instrumental and complete the job
        const correctedData = {}; // Empty object since review is assumed complete
        
        // Store the job ID for later use
        window.currentInstrumentalJobId = jobId;
        
        await showInstrumentalSelectionModal(jobId, correctedData);
        
    } catch (error) {
        console.error('Error showing instrumental selection:', error);
        showError('Error loading instrumental options: ' + error.message);
    }
}

// Debug helper function for testing timestamp parsing
window.debugTimestamp = function(timestamp) {
    console.group('🕐 Debug Timestamp Parsing:', timestamp);
    try {
        const parsed = parseServerTime(timestamp);
        const now = new Date();
        const diff = now - parsed;
        
        console.log('Original timestamp:', timestamp);
        console.log('Parsed as UTC:', parsed.toISOString());
        console.log('Parsed as local string:', parsed.toString());
        console.log('Formatted submitted time:', formatSubmittedTime({ created_at: timestamp }));
        console.log('Formatted timestamp:', formatTimestamp(timestamp));
        console.log('User timezone:', Intl.DateTimeFormat().resolvedOptions().timeZone);
        console.log('Timezone offset (minutes):', new Date().getTimezoneOffset());
        console.log('Current time UTC:', now.toISOString());
        console.log('Current time local:', now.toString());
        console.log('Difference (ms):', diff);
        console.log('Difference (seconds):', Math.floor(diff / 1000));
        console.log('Formatted duration:', formatDuration(Math.floor(Math.max(0, diff) / 1000)));
        
        if (diff < 0) {
            console.warn('⚠️ NEGATIVE DURATION DETECTED - This will show as 0s');
        }
        
        if (isNaN(parsed.getTime())) {
            console.error('⚠️ INVALID DATE DETECTED');
        }
    } catch (error) {
        console.error('Error parsing timestamp:', error);
    }
    console.groupEnd();
};

console.log('🎤 Karaoke Generator Frontend Ready!');
// Add job cloning functions after the existing admin functions

// Job cloning functions (admin only)
async function showCloneJobModal(jobId) {
    if (!currentUser || !currentUser.admin_access) {
        showError('Admin access required for job cloning');
        return;
    }
    
    try {
        showInfo('Loading clone options...');
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/jobs/${jobId}/clone-info`);
        
        if (!response) return; // Auth failed, already handled
        
        if (response.ok) {
            const result = await response.json();
            displayCloneJobModal(result);
        } else {
            const error = await response.json();
            showError('Error loading clone options: ' + error.message);
        }
    } catch (error) {
        console.error('Error loading clone options:', error);
        showError('Error loading clone options: ' + error.message);
    }
}

function displayCloneJobModal(cloneInfo) {
    const modalHtml = `
        <div id="clone-job-modal" class="modal">
            <div class="modal-content clone-job-modal-content" style="max-height: 90vh; overflow-y: auto;">
                <div class="modal-header">
                    <h3 class="modal-title">🔄 Clone Job ${cloneInfo.job_id}</h3>
                    <div class="modal-controls">
                        <button onclick="closeCloneJobModal()" class="modal-close">✕</button>
                    </div>
                </div>
                <div class="modal-body" style="max-height: calc(90vh - 60px); overflow-y: auto; padding: 20px;">
                    <div class="clone-job-info">
                        <div class="clone-source-info">
                            <h4>Source Job</h4>
                            <p><strong>Track:</strong> ${cloneInfo.artist} - ${cloneInfo.title}</p>
                            <p><strong>Current Status:</strong> ${formatStatus(cloneInfo.current_status)}</p>
                            <p><strong>Job ID:</strong> ${cloneInfo.job_id}</p>
                        </div>
                        
                        <div class="clone-options">
                            <h4>Clone At Phase</h4>
                            <p class="clone-description">Select which phase to clone the job at. This will copy all files and state up to that point.</p>
                            
                            <form id="clone-job-form" onsubmit="executeJobClone(event)">
                                <div class="clone-phases">
                                    ${createClonePhasesHtml(cloneInfo.available_phases)}
                                </div>
                                
                                <div class="clone-actions">
                                    <button type="submit" class="btn btn-primary" id="clone-submit-btn" disabled>
                                        🔄 Clone Job
                                    </button>
                                    <button type="button" onclick="closeCloneJobModal()" class="btn btn-secondary">
                                        Cancel
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Remove any existing clone modal
    const existingModal = document.getElementById('clone-job-modal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Show modal
    const modal = document.getElementById('clone-job-modal');
    modal.style.display = 'flex';
    
    // Add click outside to close
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeCloneJobModal();
        }
    });
    
    // If no phases are available, show message
    if (cloneInfo.available_phases.length === 0) {
        const phasesContainer = document.querySelector('.clone-phases');
        phasesContainer.innerHTML = '<p class="no-clone-phases">No clone points available for this job. The job needs to complete at least Phase 1 to be cloneable.</p>';
    }
}

function createClonePhasesHtml(availablePhases) {
    if (availablePhases.length === 0) {
        return '<p class="no-clone-phases">No clone points available for this job.</p>';
    }
    
    let html = '';
    
    availablePhases.forEach((phase, index) => {
        html += `
            <div class="clone-phase-option">
                <label class="clone-phase-label">
                    <input type="radio" name="clone-phase" value="${phase.phase}" 
                           onchange="handleClonePhaseSelection()" ${index === 0 ? 'checked' : ''}>
                    <div class="clone-phase-info">
                        <div class="clone-phase-name">${phase.name}</div>
                        <div class="clone-phase-description">${phase.description}</div>
                        <div class="clone-phase-badge">${formatStatus(phase.phase)}</div>
                    </div>
                </label>
            </div>
        `;
    });
    
    return html;
}

function handleClonePhaseSelection() {
    const submitBtn = document.getElementById('clone-submit-btn');
    const selectedPhase = document.querySelector('input[name="clone-phase"]:checked');
    
    if (submitBtn && selectedPhase) {
        submitBtn.disabled = false;
        submitBtn.textContent = `🔄 Clone at ${formatStatus(selectedPhase.value)}`;
    }
}

async function executeJobClone(event) {
    event.preventDefault();
    
    const selectedPhase = document.querySelector('input[name="clone-phase"]:checked');
    const submitBtn = document.getElementById('clone-submit-btn');
    
    if (!selectedPhase) {
        showError('Please select a phase to clone at');
        return;
    }
    
    // Get job ID from modal title
    const modalTitle = document.querySelector('#clone-job-modal .modal-title');
    const jobIdMatch = modalTitle.textContent.match(/Clone Job (\w+)/);
    
    if (!jobIdMatch) {
        showError('Unable to determine job ID');
        return;
    }
    
    const sourceJobId = jobIdMatch[1];
    const targetPhase = selectedPhase.value;
    
    try {
        // Disable submit button and show progress
        submitBtn.disabled = true;
        submitBtn.textContent = '🔄 Cloning...';
        
        showInfo(`Cloning job ${sourceJobId} at phase ${formatStatus(targetPhase)}...`);
        
        const requestData = {
            source_job_id: sourceJobId,
            target_phase: targetPhase
        };
        
        const response = await authenticatedFetch(`${API_BASE_URL}/admin/jobs/${sourceJobId}/clone`, {
            method: 'POST',
            body: JSON.stringify(requestData)
        });
        
        if (!response) return; // Auth failed, already handled
        
        const result = await response.json();
        
        if (result.success) {
            showSuccess(`Job cloned successfully! New job ID: ${result.new_job_id}`);
            closeCloneJobModal();
            
            // Refresh jobs list to show the cloned job
            await loadJobs();
            
        } else {
            showError('Error cloning job: ' + result.message);
        }
        
    } catch (error) {
        console.error('Error cloning job:', error);
        showError('Error cloning job: ' + error.message);
    } finally {
        // Re-enable submit button
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = '🔄 Clone Job';
        }
    }
}

function closeCloneJobModal() {
    const modal = document.getElementById('clone-job-modal');
    if (modal) {
        modal.remove();
    }
}

// Clone functionality is now integrated into the main createJobActions function above

// ... existing code ...

// Log filtering state (now server-side)
let logFilters = {
    include: '',
    exclude: '',
    level: '',
    limit: 1000,
    regex: false
};

let currentLogData = null; // Store current log response data
let logRefreshInterval = null;
let logAutoRefreshEnabled = true; // Control auto-refresh of logs

// Log filtering functions (now server-side)
async function applyLogFilters() {
    // Get current filter values
    updateFilterState();
    
    // Fetch filtered logs from server
    await fetchFilteredLogs();
}

async function fetchFilteredLogs() {
    const modalLogs = document.getElementById('modal-logs');
    if (!modalLogs) return;
    
    // Get job ID from the log tail modal or current tail job ID
    const modal = document.getElementById('log-tail-modal');
    const currentJobId = modal?.getAttribute('data-job-id') || currentTailJobId;
    if (!currentJobId) return;
    
    try {
        // Only show loading state if there are no logs currently displayed
        // This prevents disrupting the user's view during auto-refresh
        const hasExistingLogs = modalLogs.querySelector('.log-entry');
        const isInitialLoad = modalLogs.innerHTML.includes('Starting log tail') || 
                             modalLogs.innerHTML.includes('logs-loading');
        
        if (!hasExistingLogs && isInitialLoad) {
            modalLogs.innerHTML = '<p class="loading">Loading logs...</p>';
        }
        
        // Build query parameters
        const params = new URLSearchParams();
        if (logFilters.include) params.append('include', logFilters.include);
        if (logFilters.exclude) params.append('exclude', logFilters.exclude);
        if (logFilters.level) params.append('level', logFilters.level);
        if (logFilters.limit > 0) params.append('limit', logFilters.limit.toString());
        if (logFilters.regex) params.append('regex', 'true');
        
        const apiUrl = `${API_BASE_URL}/logs/${currentJobId}?${params}`;
        
        // Fetch filtered logs from server using authenticated fetch
        const response = await authenticatedFetch(apiUrl);
        
        if (!response) {
            return; // Auth failed, already handled
        }
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        currentLogData = data;
        
        // Store current scroll position before updating
        const currentScrollTop = modalLogs.scrollTop;
        const currentScrollHeight = modalLogs.scrollHeight;
        const wasAtBottom = currentScrollTop + modalLogs.clientHeight >= currentScrollHeight - 10;
        
        // Render the filtered logs
        renderServerFilteredLogs(data.logs || []);
        
        // Update stats
        updateServerFilterStats(data);
        
        // Restore scroll position or auto-scroll
        if (autoScrollEnabled || wasAtBottom) {
            // If auto-scroll is enabled OR user was at bottom, scroll to bottom
            modalLogs.scrollTop = modalLogs.scrollHeight;
        } else {
            // Try to maintain relative scroll position
            const newScrollHeight = modalLogs.scrollHeight;
            const scrollRatio = currentScrollTop / Math.max(currentScrollHeight, 1);
            modalLogs.scrollTop = scrollRatio * newScrollHeight;
        }
        
    } catch (error) {
        console.error('Error fetching filtered logs:', error);
        modalLogs.innerHTML = `<p class="error">Error loading logs: ${error.message}</p>`;
    }
}

function updateFilterState() {
    // Update filters from form inputs
    logFilters.include = document.getElementById('log-include-filter')?.value || '';
    logFilters.exclude = document.getElementById('log-exclude-filter')?.value || '';
    logFilters.level = document.getElementById('log-level-filter')?.value || '';
    logFilters.limit = parseInt(document.getElementById('log-limit-input')?.value || '1000');
    logFilters.regex = document.getElementById('regex-mode-btn')?.classList.contains('active') || false;
}

function renderServerFilteredLogs(logEntries) {
    const modalLogs = document.getElementById('modal-logs');
    if (!modalLogs) return;
    
    if (logEntries.length === 0) {
        modalLogs.innerHTML = '<p class="no-logs">No logs match the current filters.</p>';
        return;
    }
    
    // Generate HTML for log entries
    const logsHTML = logEntries.map(logEntry => {
        const timestamp = parseServerTime(logEntry.timestamp).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
        const levelClass = logEntry.level.toLowerCase();
        
        return `<div class="log-entry log-${levelClass}">
            <span class="log-timestamp">${timestamp}</span>
            <span class="log-level">${logEntry.level}</span>
            <span class="log-message">${escapeHtml(logEntry.message)}</span>
        </div>`;
    }).join('');
    
    modalLogs.innerHTML = logsHTML;
}

function updateServerFilterStats(data) {
    const statsElement = document.getElementById('filter-stats');
    if (!statsElement) return;
    
    const totalCount = data.total_count || 0;
    const filtersApplied = data.filters_applied || {};
    
    let statusText = `Showing ${totalCount} logs`;
    
    // Add filter info
    const activeFilters = [];
    if (filtersApplied.include) activeFilters.push(`include:"${filtersApplied.include}"`);
    if (filtersApplied.exclude) activeFilters.push(`exclude:"${filtersApplied.exclude}"`);
    if (filtersApplied.level) activeFilters.push(`level:${filtersApplied.level}+`);
    if (filtersApplied.limit && filtersApplied.limit < 10000) activeFilters.push(`limit:${filtersApplied.limit}`);
    if (filtersApplied.regex) activeFilters.push('regex');
    
    if (activeFilters.length > 0) {
        statusText += ` (filtered: ${activeFilters.join(', ')})`;
    }
    
    statsElement.textContent = statusText;
}

// Simplified filter control functions for server-side filtering

function toggleRegexMode() {
    const button = document.getElementById('regex-mode-btn');
    if (!button) return;
    
    const isActive = button.classList.toggle('active');
    
    // Update button appearance
    if (isActive) {
        button.style.fontWeight = 'bold';
        button.title = 'Regex mode enabled - click to disable';
    } else {
        button.style.fontWeight = 'normal';
        button.title = 'Toggle regex mode';
    }
    
    // Reapply filters
    applyLogFilters();
}

function clearAllFilters() {
    // Clear main filter inputs
    const includeFilter = document.getElementById('log-include-filter');
    const excludeFilter = document.getElementById('log-exclude-filter');
    const levelFilter = document.getElementById('log-level-filter');
    const limitInput = document.getElementById('log-limit-input');
    
    if (includeFilter) includeFilter.value = '';
    if (excludeFilter) excludeFilter.value = '';
    if (levelFilter) levelFilter.value = '';
    if (limitInput) limitInput.value = '1000';
    
    // Clear regex toggle
    const regexBtn = document.getElementById('regex-mode-btn');
    if (regexBtn) {
        regexBtn.classList.remove('active');
        regexBtn.style.fontWeight = 'normal';
        regexBtn.title = 'Toggle regex mode';
    }
    
    // Reset filter state
    logFilters = {
        include: '',
        exclude: '',
        level: '',
        limit: 1000,
        regex: false
    };
    
    // Reapply filters (will show all logs with default limit)
    applyLogFilters();
    
    showInfo('All filters cleared');
}

async function refreshLogs() {
    // Manual refresh of logs with current filters
    try {
        const refreshBtn = document.querySelector('button[onclick="refreshLogs()"]');
        if (refreshBtn) {
            const originalText = refreshBtn.innerHTML;
            refreshBtn.innerHTML = '🔄';
            refreshBtn.disabled = true;
            
            await applyLogFilters();
            
            refreshBtn.innerHTML = originalText;
            refreshBtn.disabled = false;
            
            showInfo('Logs refreshed manually');
        } else {
            await applyLogFilters();
        }
    } catch (error) {
        console.error('Error during manual refresh:', error);
        showError('Failed to refresh logs: ' + error.message);
    }
}

function setLogLimit(value) {
    const limitInput = document.getElementById('log-limit-input');
    if (limitInput) {
        limitInput.value = value;
        applyLogFilters();
    }
}

function toggleLogAutoRefresh() {
    logAutoRefreshEnabled = !logAutoRefreshEnabled;
    
    const autoRefreshBtn = document.getElementById('log-auto-refresh-btn');
    if (autoRefreshBtn) {
        if (logAutoRefreshEnabled) {
            autoRefreshBtn.classList.add('toggle-active');
            autoRefreshBtn.textContent = '🔄 Refresh';
            autoRefreshBtn.title = 'Auto-refresh enabled - click to disable';
            showInfo('Log auto-refresh enabled');
        } else {
            autoRefreshBtn.classList.remove('toggle-active');
            autoRefreshBtn.textContent = '⏸️ Paused';
            autoRefreshBtn.title = 'Auto-refresh disabled - click to enable';
            showInfo('Log auto-refresh paused');
        }
    }
}

// Modify the existing loadLogTailData function to store logs and apply filters

// YouTube Authentication Functions
async function checkYouTubeAuthStatus() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/youtube/auth-status`);
        const data = await response.json();
        
        if (data.success) {
            return data;
        } else {
            console.warn('YouTube auth status check failed:', data.message);
            return { authenticated: false, message: data.message };
        }
    } catch (error) {
        console.error('Error checking YouTube auth status:', error);
        return { authenticated: false, message: 'Error checking authentication' };
    }
}

async function authenticateWithYouTube() {
    try {
        // Get the authorization URL
        const response = await authenticatedFetch(`${API_BASE_URL}/youtube/auth-url`);
        const data = await response.json();
        
        if (!data.success) {
            showError(`YouTube authentication setup error: ${data.message}`);
            return false;
        }
        
        // Open authorization URL in a popup
        const popup = window.open(
            data.authorization_url,
            'youtube_auth',
            'width=500,height=600,scrollbars=yes,resizable=yes'
        );
        
        // Listen for the popup to send a success message
        return new Promise((resolve) => {
            const messageHandler = (event) => {
                if (event.data && event.data.type === 'youtube_auth_success') {
                    window.removeEventListener('message', messageHandler);
                    popup.close();
                    showSuccess('YouTube authentication successful!');
                    resolve(true);
                }
            };
            
            window.addEventListener('message', messageHandler);
            
            // Check if popup was closed manually
            const checkClosed = setInterval(() => {
                if (popup.closed) {
                    clearInterval(checkClosed);
                    window.removeEventListener('message', messageHandler);
                    resolve(false);
                }
            }, 1000);
            
            // Timeout after 10 minutes
            setTimeout(() => {
                clearInterval(checkClosed);
                window.removeEventListener('message', messageHandler);
                if (!popup.closed) {
                    popup.close();
                }
                resolve(false);
            }, 10 * 60 * 1000);
        });
        
    } catch (error) {
        console.error('Error starting YouTube authentication:', error);
        showError('Error starting YouTube authentication');
        return false;
    }
}

async function revokeYouTubeAuth() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/youtube/auth`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess('YouTube authentication revoked');
            return true;
        } else {
            showError(`Error revoking YouTube authentication: ${data.message}`);
            return false;
        }
    } catch (error) {
        console.error('Error revoking YouTube authentication:', error);
        showError('Error revoking YouTube authentication');
        return false;
    }
}

function displayYouTubeAuthStatus(authStatus) {
    const statusContainer = document.getElementById('youtube-auth-status');
    
    if (authStatus.authenticated) {
        statusContainer.innerHTML = `
            <div class="youtube-auth-info authenticated">
                ✅ Authenticated with YouTube
            </div>
            <div class="youtube-auth-actions">
                <button onclick="revokeYouTubeAuthAndUpdate()" class="youtube-revoke-btn">
                    Revoke
                </button>
            </div>
        `;
    } else {
        statusContainer.innerHTML = `
            <div class="youtube-auth-info not-authenticated">
                ⚠️ Not authenticated - videos will be generated without YouTube upload
            </div>
            <div class="youtube-auth-actions">
                <button onclick="authenticateYouTubeAndUpdate()" class="youtube-auth-btn">
                    🔑 Authenticate with YouTube
                </button>
            </div>
        `;
    }
}

async function authenticateYouTubeAndUpdate() {
    const authBtn = document.querySelector('.youtube-auth-btn');
    const originalText = authBtn.textContent;
    
    authBtn.disabled = true;
    authBtn.textContent = '🔄 Authenticating...';
    
    try {
        const success = await authenticateWithYouTube();
        
        if (success) {
            // Refresh auth status
            const newStatus = await checkYouTubeAuthStatus();
            displayYouTubeAuthStatus(newStatus);
        }
    } finally {
        // Reset button if it still exists (might have been replaced)
        const currentBtn = document.querySelector('.youtube-auth-btn');
        if (currentBtn) {
            currentBtn.disabled = false;
            currentBtn.textContent = originalText;
        }
    }
}

async function revokeYouTubeAuthAndUpdate() {
    const success = await revokeYouTubeAuth();
    
    if (success) {
        // Refresh auth status
        const newStatus = await checkYouTubeAuthStatus();
        displayYouTubeAuthStatus(newStatus);
    }
}