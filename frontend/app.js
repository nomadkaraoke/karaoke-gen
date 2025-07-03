// Configuration
const API_BASE_URL = 'https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api';

// Global state
let autoRefreshInterval = null;
let logTailInterval = null;
let currentTailJobId = null;
let logFontSizeIndex = 2; // Default to 'font-md'
let autoScrollEnabled = true;

// Initialize the application
document.addEventListener('DOMContentLoaded', function() {
    // Debug timezone information
    console.log('🌍 Timezone Debug Info:', {
        userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        localTime: new Date().toISOString(),
        localTimeString: new Date().toString(),
        timezoneOffset: new Date().getTimezoneOffset(),
        userAgent: navigator.userAgent.substring(0, 100)
    });
    
    loadJobs();
    
    // Auto-refresh checkbox handler
    const autoRefreshCheckbox = document.getElementById('auto-refresh');
    if (autoRefreshCheckbox) {
        autoRefreshCheckbox.addEventListener('change', function() {
            if (this.checked) {
                startAutoRefresh();
                showInfo('Auto-refresh enabled - jobs will update every 5 seconds');
            } else {
                stopAutoRefresh();
                showInfo('Auto-refresh disabled');
            }
        });
        
        // Check if auto-refresh should be enabled on page load
        if (autoRefreshCheckbox.checked) {
            startAutoRefresh();
        }
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
        }
    });
});

function startAutoRefresh() {
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
    // Store current scroll position
    const currentScrollY = window.scrollY;
    const currentScrollX = window.scrollX;
    
    return loadJobs().then(() => {
        // Restore scroll position after update
        window.scrollTo(currentScrollX, currentScrollY);
    }).catch(error => {
        console.error('Error in loadJobsWithoutScroll:', error);
        // Don't throw the error to prevent auto-refresh from stopping
        showError('Auto-refresh failed: ' + error.message);
    });
}

async function loadJobs() {
    try {
        console.log('Loading jobs from API...');
        const response = await fetch(`${API_BASE_URL}/jobs`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const jobs = await response.json();
        console.log(`Loaded ${Object.keys(jobs).length} jobs`);
        
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
        if (['queued', 'processing_audio', 'transcribing', 'rendering'].includes(status)) {
            stats.processing++;
        } else if (status === 'awaiting_review') {
            stats.awaiting_review++;
        } else if (status === 'complete') {
            stats.complete++;
        } else if (status === 'error') {
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
    
    // Format track info for display
    const trackInfo = (job.artist && job.title) 
        ? `${job.artist} - ${job.title}` 
        : (job.url ? 'URL Processing' : 'Unknown Track');
    
    // Get timeline information
    const timelineInfo = createTimelineInfoHtml(job);
    const submittedTime = formatSubmittedTime(job);
    const totalDuration = getTotalJobDuration(job);
    const multiStageProgressBar = createMultiStageProgressBar(job);
    
    return `
        <div class="job" data-job-id="${jobId}">
            <div class="job-row">
                <div class="job-main-info">
                    <div class="job-header">
                        <div class="job-title-section">
                            <div class="job-header-line">
                                <span class="job-id">🎵 Job ${jobId}</span>
                                <div class="job-status">
                                    <span class="status-badge status-${status}">${formatStatus(status)}</span>
                                </div>
                            </div>
                            <div class="job-track-info">
                                <span class="track-name">${trackInfo}</span>
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
                    <button onclick="showTimelineModal('${jobId}')" class="btn btn-secondary" title="View detailed timeline">
                        ⏱️ Timeline
                    </button>
                    <button onclick="tailJobLogs('${jobId}')" class="btn btn-info">
                        📜 View Logs
                    </button>
                </div>
            </div>
        </div>
    `;
}

function formatSubmittedTime(job) {
    // Try timeline data first
    if (job.timeline && job.timeline.length > 0) {
        const submitTime = parseServerTime(job.timeline[0].started_at);
        const now = new Date();
        const diffHours = (now - submitTime) / (1000 * 60 * 60);
        
        if (diffHours < 24) {
            return submitTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        } else {
            return submitTime.toLocaleDateString([], {month: 'short', day: 'numeric'}) + ' ' + 
                   submitTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        }
    }
    
    // Fallback to created_at
    if (job.created_at) {
        const submitTime = parseServerTime(job.created_at);
        const now = new Date();
        const diffHours = (now - submitTime) / (1000 * 60 * 60);
        
        if (diffHours < 24) {
            return submitTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        } else {
            return submitTime.toLocaleDateString([], {month: 'short', day: 'numeric'}) + ' ' + 
                   submitTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        }
    }
    
    return 'Unknown';
}

function getTotalJobDuration(job) {
    // Try timeline summary first
    const timeline_summary = job.timeline_summary;
    if (timeline_summary && timeline_summary.total_duration_formatted) {
        return timeline_summary.total_duration_formatted;
    }
    
    // Try calculating from timeline data directly
    if (job.timeline && job.timeline.length > 0) {
        try {
            const startTime = parseServerTime(job.timeline[0].started_at);
            const now = new Date();
            const durationMs = now - startTime;
            
            // Debug logging for timezone issues
            if (durationMs < 0) {
                console.warn('Negative duration detected:', {
                    startTime: startTime.toISOString(),
                    now: now.toISOString(),
                    originalTimestamp: job.timeline[0].started_at,
                    durationMs,
                    userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone
                });
            }
            
            const durationSeconds = Math.floor(Math.max(0, durationMs) / 1000);
            return formatDuration(durationSeconds);
        } catch (error) {
            console.error('Error calculating timeline duration:', error, job.timeline[0]);
        }
    }
    
    // Fallback to calculating from created_at if no timeline data
    if (job.created_at) {
        try {
            const startTime = parseServerTime(job.created_at);
            const now = new Date();
            const durationMs = now - startTime;
            
            // Debug logging for timezone issues
            if (durationMs < 0) {
                console.warn('Negative duration detected from created_at:', {
                    startTime: startTime.toISOString(),
                    now: now.toISOString(),
                    originalTimestamp: job.created_at,
                    durationMs,
                    userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone
                });
            }
            
            const durationSeconds = Math.floor(Math.max(0, durationMs) / 1000);
            return formatDuration(durationSeconds);
        } catch (error) {
            console.error('Error calculating created_at duration:', error, job.created_at);
        }
    }
    
    return 'Unknown';
}

function createMultiStageProgressBar(job) {
    const timeline_summary = job.timeline_summary;
    const currentStatus = job.status || 'unknown';
    const timeline = job.timeline || [];
    
    // Define all possible phases in order with cleaner labels
    const allPhases = [
        { key: 'queued', label: 'Queued', shortLabel: 'Queue' },
        { key: 'processing', label: 'Processing', shortLabel: 'Process' },
        { key: 'awaiting_review', label: 'Review', shortLabel: 'Review' },
        { key: 'reviewing', label: 'Reviewing', shortLabel: 'Review' },
        { key: 'rendering', label: 'Rendering', shortLabel: 'Render' },
        { key: 'complete', label: 'Complete', shortLabel: 'Done' }
    ];
    
    let html = '<div class="job-progress-enhanced">';
    html += '<div class="multi-stage-progress-bar">';
    
    if (timeline_summary && timeline_summary.phase_durations) {
        const phaseDurations = timeline_summary.phase_durations;
        const totalDuration = timeline_summary.total_duration_seconds || 1;
        
        // Create segments for each phase that has occurred or is occurring
        let accumulatedWidth = 0;
        
        allPhases.forEach((phase) => {
            const duration = phaseDurations[phase.key];
            if (duration !== undefined && duration > 0) {
                const widthPercent = Math.max((duration / totalDuration) * 100, 8); // Minimum 8% width
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
    const phaseOrder = ['queued', 'processing', 'awaiting_review', 'reviewing', 'rendering', 'complete'];
    const currentIndex = phaseOrder.indexOf(currentStatus);
    const phaseIndex = phaseOrder.indexOf(phaseKey);
    
    // Show phases that come after the current status (except 'complete' which we handle specially)
    return phaseIndex > currentIndex && phaseKey !== 'complete';
}

function isNextPhase(phaseKey, currentStatus) {
    const phaseOrder = ['queued', 'processing', 'awaiting_review', 'reviewing', 'rendering', 'complete'];
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
        'rendering': isUpcoming ? '#66d9a3' : '#28a745',
        'complete': isUpcoming ? '#66d9a3' : '#28a745',
        'error': isUpcoming ? '#ff8a9a' : '#dc3545'
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
        'awaiting_review': '⏸️',
        'reviewing': '👁️',
        'rendering': '🎬',
        'complete': '✅',
        'error': '❌'
    };
    return icons[phase] || '📋';
}

// Timeline Modal Functions
async function showTimelineModal(jobId) {
    try {
        showInfo('Loading timeline data...');
        
        const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/timeline`);
        
        if (!response.ok) {
            // If timeline endpoint fails, try to get basic job data and create a simple timeline
            console.warn('Timeline endpoint failed, trying basic job data...');
            const jobResponse = await fetch(`${API_BASE_URL}/jobs/${jobId}`);
            
            if (jobResponse.ok) {
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

function createTimelineVisualizationHtml(timelineData) {
    const timeline = timelineData.timeline || [];
    const summary = timelineData.timeline_summary || {};
    const metrics = timelineData.performance_metrics || {};
    
    let html = '';
    
    // Summary cards
    html += `
        <div class="timeline-summary">
            <div class="timeline-summary-cards">
                <div class="timeline-card">
                    <div class="timeline-card-value">${metrics.total_processing_time || '0s'}</div>
                    <div class="timeline-card-label">Total Time</div>
                </div>
                <div class="timeline-card">
                    <div class="timeline-card-value">${metrics.phases_completed || 0}</div>
                    <div class="timeline-card-label">Phases Complete</div>
                </div>
                <div class="timeline-card">
                    <div class="timeline-card-value">${formatStatus(timelineData.current_status)}</div>
                    <div class="timeline-card-label">Current Status</div>
                </div>
                ${metrics.estimated_remaining ? `
                    <div class="timeline-card">
                        <div class="timeline-card-value">${metrics.estimated_remaining}</div>
                        <div class="timeline-card-label">Est. Remaining</div>
                    </div>
                ` : ''}
            </div>
        </div>
    `;
    
    // Visual timeline
    if (timeline.length > 0) {
        html += '<div class="timeline-visualization">';
        html += '<h4>Phase Timeline</h4>';
        html += '<div class="timeline-chart">';
        
        const totalDuration = summary.total_duration_seconds || 1;
        
        timeline.forEach((phase, index) => {
            const duration = phase.duration_seconds;
            const startTime = parseServerTime(phase.started_at);
            const endTime = phase.ended_at ? parseServerTime(phase.ended_at) : new Date();
            const isActive = !phase.ended_at;
            
            // Calculate width percentage for visualization
            const widthPercent = duration ? (duration / totalDuration) * 100 : (isActive ? 10 : 0);
            
            html += `
                <div class="timeline-phase-bar ${isActive ? 'active' : ''}" 
                     style="width: ${Math.max(widthPercent, 5)}%; background-color: ${getPhaseColor(phase.status)}">
                    <div class="timeline-phase-info">
                        <div class="timeline-phase-name">
                            ${getPhaseIcon(phase.status)} ${formatStatus(phase.status)}
                        </div>
                        <div class="timeline-phase-duration">
                            ${duration ? formatDuration(duration) : (isActive ? 'In Progress' : 'Unknown')}
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
                <div>Status</div>
            </div>
        `;
        
        timeline.forEach((phase) => {
            const startTime = formatDetailedTimestamp(phase.started_at);
            const endTime = phase.ended_at ? formatDetailedTimestamp(phase.ended_at) : 'In Progress';
            const duration = phase.duration_seconds ? formatDuration(phase.duration_seconds) : 'In Progress';
            const isActive = !phase.ended_at;
            
            html += `
                <div class="timeline-table-row ${isActive ? 'active' : ''}">
                    <div class="timeline-phase-cell">
                        ${getPhaseIcon(phase.status)} ${formatStatus(phase.status)}
                    </div>
                    <div>${startTime}</div>
                    <div>${endTime}</div>
                    <div>${duration}</div>
                    <div>
                        <span class="status-indicator" style="background-color: ${getPhaseColor(phase.status)}"></span>
                        ${isActive ? 'Active' : 'Complete'}
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
        html += '</div>';
    }
    
    // Phase transitions (if any gaps between phases)
    if (timelineData.phase_transitions && timelineData.phase_transitions.length > 0) {
        html += '<div class="timeline-transitions">';
        html += '<h4>Phase Transitions</h4>';
        html += '<div class="transitions-list">';
        
        timelineData.phase_transitions.forEach(transition => {
            if (transition.transition_duration_seconds > 1) { // Only show significant gaps
                html += `
                    <div class="transition-item">
                        <span class="transition-phases">
                            ${formatStatus(transition.from_status)} → ${formatStatus(transition.to_status)}
                        </span>
                        <span class="transition-duration">
                            Gap: ${formatDuration(transition.transition_duration_seconds)}
                        </span>
                    </div>
                `;
            }
        });
        
        html += '</div>';
        html += '</div>';
    }
    
    return html;
}

function formatDetailedTimestamp(isoString) {
    const date = parseServerTime(isoString);
    return date.toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
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
        // For reviewing status, go directly to the review URL
        const reviewUrl = `https://lyrics.nomadkaraoke.com/?baseApiUrl=${API_BASE_URL}/corrections/${jobId}`;
        actions.push(`<a href="${reviewUrl}" target="_blank" class="btn btn-success">📝 Continue Review</a>`);
    }
    
    if (status === 'complete') {
        actions.push(`<button onclick="downloadVideo('${jobId}')" class="btn btn-primary">📥 Download Video</button>`);
        actions.push(`<button onclick="showFilesModal('${jobId}')" class="btn btn-info">📁 All Files</button>`);
    }
    
    if (status === 'error') {
        actions.push(`<button onclick="retryJob('${jobId}')" class="btn btn-warning">🔄 Retry</button>`);
    }
    
    // Always available actions
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
        currentTailJobId = null;
        
        // Set up modal content
        modalJobId.textContent = jobId;
        modalLogs.innerHTML = '<div class="logs-loading">Starting log tail...</div>';
        
        // Apply current font size
        updateLogsFontSize();
        
        // Reset auto-scroll state
        autoScrollEnabled = true;
        const autoScrollBtn = document.getElementById('auto-scroll-btn');
        if (autoScrollBtn) {
            autoScrollBtn.classList.add('toggle-active');
            autoScrollBtn.textContent = '🔄 Auto';
            autoScrollBtn.title = 'Auto-scroll enabled - click to disable';
        }
        
        // Force reflow and show modal
        modal.offsetHeight; // Trigger reflow
        modal.style.display = 'flex';
        
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
    
    // Stop any log tailing
    stopLogTail();
    
    // Reset auto-scroll to enabled for next time
    autoScrollEnabled = true;
    const autoScrollBtn = document.getElementById('auto-scroll-btn');
    if (autoScrollBtn) {
        autoScrollBtn.classList.add('toggle-active');
        autoScrollBtn.textContent = '🔄 Auto';
        autoScrollBtn.title = 'Auto-scroll enabled - click to disable';
    }
    
    // Clear modal content to ensure fresh state
    const modalLogs = document.getElementById('modal-logs');
    if (modalLogs) {
        modalLogs.innerHTML = '';
    }
    
    const modalJobId = document.getElementById('modal-job-id');
    if (modalJobId) {
        modalJobId.textContent = '';
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
            const statusResponse = await fetch(`${API_BASE_URL}/jobs/${jobId}`);
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
    
    try {
        const [statusResponse, logsResponse] = await Promise.all([
            fetch(`${API_BASE_URL}/jobs/${jobId}`),
            fetch(`${API_BASE_URL}/logs/${jobId}`)
        ]);
        
        const status = await statusResponse.json();
        const logs = await logsResponse.json();
        
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
        
        // Update logs
        if (logs.length === 0) {
            modalLogs.innerHTML = '<p class="no-logs">No logs available yet...</p>';
            return;
        }
        
        const logsHTML = logs.map(log => {
            const timestamp = parseServerTime(log.timestamp).toLocaleTimeString();
            const levelClass = log.level.toLowerCase();
            return `<div class="log-entry log-${levelClass}">
                <span class="log-timestamp">${timestamp}</span>
                <span class="log-level">${log.level}</span>
                <span class="log-message">${escapeHtml(log.message)}</span>
            </div>`;
        }).join('');
        
        modalLogs.innerHTML = logsHTML;
        
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
        const response = await fetch(`${API_BASE_URL}/admin/clear-errors`, {
            method: 'POST'
        });
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
        const response = await fetch(`${API_BASE_URL}/admin/cache/stats`);
        const stats = await response.json();
        
        if (response.ok) {
            showCacheStatsModal(stats);
        } else {
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
        const response = await fetch(`${API_BASE_URL}/admin/cache/clear`, {
            method: 'POST'
        });
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
        
        const response = await fetch(`${API_BASE_URL}/admin/cache/warm`, {
            method: 'POST'
        });
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

async function loadAudioShakeCache() {
    try {
        const response = await fetch(`${API_BASE_URL}/admin/cache/audioshake`);
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
        const response = await fetch(`${API_BASE_URL}/admin/cache/audioshake/${audioHash}`, {
            method: 'DELETE'
        });
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
            const timestamp = parseServerTime(item.timestamp).toLocaleString();
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
    
    try {
        const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/retry`, {
            method: 'POST'
        });
        const result = await response.json();
        
        if (result.status === 'success') {
            showSuccess(`Job ${jobId} retry initiated`);
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
        const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`, {
            method: 'DELETE'
        });
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
        const response = await fetch(`${API_BASE_URL}/review/${jobId}/start`, {
            method: 'POST'
        });
        
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

function downloadVideo(jobId) {
    window.open(`${API_BASE_URL}/jobs/${jobId}/download`, '_blank');
}

// Form submission
async function submitJob() {
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
            // Load default styles automatically
            const [defaultStylesResponse, defaultArchiveResponse] = await Promise.all([
                fetch('./karaoke-prep-styles-nomad.json'),
                fetch('./nomadstyles.zip')
            ]);
            
            if (defaultStylesResponse.ok && defaultArchiveResponse.ok) {
                const defaultStylesJson = await defaultStylesResponse.text();
                const defaultArchiveBlob = await defaultArchiveResponse.blob();
                
                // Create default style files
                const defaultStylesFile = new File([new Blob([defaultStylesJson], { type: 'application/json' })], 'karaoke-prep-styles-nomad.json', { type: 'application/json' });
                const defaultArchiveFile = new File([defaultArchiveBlob], 'nomadstyles.zip', { type: 'application/zip' });
                
                formData.append('styles_file', defaultStylesFile);
                formData.append('styles_archive', defaultArchiveFile);
            }
        } catch (error) {
            console.warn('Could not load default styles, proceeding without them:', error);
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
    
    const submitBtn = document.querySelector('.submit-btn');
    const originalText = submitBtn.textContent;
    
    try {
        submitBtn.textContent = 'Uploading...';
        submitBtn.disabled = true;
        
        const response = await fetch(`${API_BASE_URL}/submit-file`, {
            method: 'POST',
            body: formData
        });
        console.log('submitJob response from /submit-file submission');
        console.log(response);
        
        const result = await response.json();
        console.log('submitJob JSON result from /submit-file submission');
        console.log(result);
        
        if (response.status === 200) {
            const usingCustomStyles = customStylesVisible && (stylesFile || stylesArchive);
            const stylesMessage = usingCustomStyles ? ' with custom styles' : ' with default Nomad styles';
            showSuccess(`Job submitted successfully${stylesMessage}! Job ID: ${result.job_id}`);
            
            // Clear form
            document.getElementById('audio-file').value = '';
            document.getElementById('artist').value = '';
            document.getElementById('title').value = '';
            
            // Only clear custom styles if they were visible
            if (customStylesVisible) {
                document.getElementById('styles-file').value = '';
                document.getElementById('styles-archive').value = '';
            }
            
            // Refresh jobs list immediately and scroll to it
            showInfo('Refreshing job list...');
            const jobs = await loadJobs();
            
            if (jobs) {
                // Scroll to jobs section to show the new job
                const jobsSection = document.querySelector('.jobs-section');
                if (jobsSection) {
                    jobsSection.scrollIntoView({ behavior: 'smooth' });
                }
                
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
        } else {
            showError(result.message || 'Failed to submit job');
        }
        
    } catch (error) {
        console.error('Error submitting job:', error);
        showError('Failed to submit job: ' + error.message);
    } finally {
        submitBtn.textContent = originalText;
        submitBtn.disabled = false;
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
    // Assume server timestamps are in UTC, ensure proper parsing
    if (!timestamp) return new Date();
    
    // Convert to string if it's not already
    const timestampStr = String(timestamp);
    
    // If timestamp doesn't end with 'Z' or have timezone info, treat as UTC
    if (!timestampStr.includes('Z') && !timestampStr.includes('+') && !timestampStr.includes('-')) {
        // Add 'Z' to explicitly mark as UTC
        return new Date(timestampStr + 'Z');
    }
    
    // If it already has timezone info, parse normally
    return new Date(timestampStr);
}

function formatStatus(status) {
    const statusMap = {
        'queued': 'Queued',
        'processing_audio': 'Processing Audio',
        'transcribing': 'Transcribing Lyrics',
        'awaiting_review': 'Awaiting Review',
        'rendering': 'Rendering Video',
        'complete': 'Complete',
        'error': 'Error'
    };
    return statusMap[status] || status;
}

function formatTimestamp(timestamp) {
    return parseServerTime(timestamp).toLocaleString();
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
    const duration = calculateDuration(job.created_at);
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

function showInfo(message) {
    showNotification(message, 'info');
}

function showNotification(message, type) {
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
    
    // Auto-remove after 5 seconds (increased from 3 for better UX)
    setTimeout(() => {
        notification.remove();
    }, 5000);
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

// Files modal functions
async function showFilesModal(jobId) {
    try {
        showInfo('Loading files...');
        
        const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/files`);
        
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
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
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
        const url = `${API_BASE_URL}/jobs/${jobId}/files/${encodeURIComponent(filePath)}`;
        
        // Create a temporary link and click it
        const link = document.createElement('a');
        link.href = url;
        link.download = fileName;
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
        showInfo('Creating download package...');
        
        const url = `${API_BASE_URL}/jobs/${jobId}/download-all`;
        
        // Create a temporary link and click it
        const link = document.createElement('a');
        link.href = url;
        link.download = `karaoke-${jobId}-complete.zip`;
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
    const videoUrl = `${API_BASE_URL}/jobs/${jobId}/files/${encodeURIComponent(filePath)}`;
    
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
                                class="btn btn-primary">📥 Download</button>
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
    const audioUrl = `${API_BASE_URL}/jobs/${jobId}/files/${encodeURIComponent(filePath)}`;
    
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
                                class="btn btn-primary">📥 Download</button>
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

// Debug helper function for testing timestamp parsing
window.debugTimestamp = function(timestamp) {
    console.group('🕐 Debug Timestamp Parsing:', timestamp);
    try {
        const parsed = parseServerTime(timestamp);
        const now = new Date();
        const diff = now - parsed;
        
        console.log('Original timestamp:', timestamp);
        console.log('Parsed as:', parsed.toISOString());
        console.log('Parsed local string:', parsed.toString());
        console.log('Current time:', now.toISOString());
        console.log('Current local string:', now.toString());
        console.log('Difference (ms):', diff);
        console.log('Difference (seconds):', Math.floor(diff / 1000));
        console.log('Formatted duration:', formatDuration(Math.floor(Math.max(0, diff) / 1000)));
        
        if (diff < 0) {
            console.warn('⚠️ NEGATIVE DURATION DETECTED - This will show as 0s');
        }
    } catch (error) {
        console.error('Error parsing timestamp:', error);
    }
    console.groupEnd();
};

console.log('🎤 Karaoke Generator Frontend Ready!');
console.log('💡 Use debugTimestamp("your-timestamp-here") to test timestamp parsing'); 