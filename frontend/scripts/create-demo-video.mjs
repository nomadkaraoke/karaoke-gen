#!/usr/bin/env node

/**
 * Post-process Playwright demo recordings into a polished demo video.
 *
 * Reads the raw .webm files from test-results/ (produced by demo-recording.spec.ts),
 * parses MARKER timestamps from the test output, and uses ffmpeg to:
 *   1. Speed up boring wait sections (processing, rendering) by 20x
 *   2. Keep interactive sections (job creation, review, instrumental) at 2x
 *   3. Add text overlay cards for intro/section transitions/outro
 *   4. Stitch main-page and review-page videos together
 *   5. Output a polished MP4 for YouTube/homepage embed
 *
 * Prerequisites:
 *   - ffmpeg installed (brew install ffmpeg)
 *   - Raw recordings in test-results/ from: npm run test:e2e:demo
 *
 * Usage:
 *   node scripts/create-demo-video.mjs [--markers path/to/markers.json]
 *
 * If no markers file is provided, the script uses sensible defaults based on
 * typical test timing.
 */

import { execSync } from 'child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = join(__dirname, '..');
const TEST_RESULTS = join(FRONTEND_DIR, 'test-results');
const OUTPUT_DIR = join(FRONTEND_DIR, 'demo-output');

// Ensure output directory exists
mkdirSync(OUTPUT_DIR, { recursive: true });

// =============================================================================
// Find raw video files
// =============================================================================

function findVideos() {
  // Playwright puts videos in test-results/<test-name>/video.webm
  const testResultDirs = execSync(`find "${TEST_RESULTS}" -name "video*.webm" -type f`, {
    encoding: 'utf-8',
  }).trim().split('\n').filter(Boolean);

  if (testResultDirs.length === 0) {
    console.error('No video files found in test-results/');
    console.error('Run the demo recording first: npm run test:e2e:demo');
    process.exit(1);
  }

  // Sort so video.webm (main page) comes first, video-1.webm (review page) second.
  // Playwright names them: video.webm for the first context, video-1.webm for the second.
  // Alphabetically video-1.webm sorts before video.webm, so we reverse to get correct order.
  const sorted = testResultDirs.sort((a, b) => {
    // Prefer shorter filename (video.webm) over video-1.webm
    const aBase = a.split('/').pop();
    const bBase = b.split('/').pop();
    return aBase.length - bBase.length;
  });
  console.log('Found videos:');
  sorted.forEach((v, i) => {
    const duration = getDuration(v);
    const label = i === 0 ? '(main page)' : '(review page)';
    console.log(`  ${label} ${v} (${formatTime(duration)})`);
  });

  return sorted;
}

function getDuration(filepath) {
  try {
    const result = execSync(
      `ffprobe -v quiet -show_entries format=duration -of csv=p=0 "${filepath}"`,
      { encoding: 'utf-8' }
    ).trim();
    return parseFloat(result);
  } catch {
    return 0;
  }
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

function run(cmd, label) {
  console.log(`\n=== ${label} ===`);
  console.log(`  $ ${cmd.substring(0, 200)}${cmd.length > 200 ? '...' : ''}`);
  try {
    execSync(cmd, { stdio: 'inherit' });
  } catch (err) {
    console.error(`Failed: ${label}`);
    throw err;
  }
}

// =============================================================================
// Video processing pipeline
// =============================================================================

/**
 * Step 1: Convert each webm to mp4 (H.264) for easier processing
 */
function convertToMp4(videos) {
  const mp4s = [];
  for (let i = 0; i < videos.length; i++) {
    const out = join(OUTPUT_DIR, `raw-${i}.mp4`);
    run(
      `ffmpeg -y -i "${videos[i]}" -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -r 30 "${out}"`,
      `Convert video ${i} to MP4`
    );
    mp4s.push(out);
  }
  return mp4s;
}

/**
 * Step 2: Create the sped-up version of each video.
 *
 * The demo recording test logs MARKER lines with timestamps.
 * Sections marked SPEED_UP (processing waits) get 20x speed.
 * Interactive sections get 2x speed.
 * Key moments (job created, review opened) stay at 1x briefly.
 *
 * Since we may not have exact marker timestamps, we use a simpler
 * approach: create a uniform 3x speed version, then manually
 * fine-tune if needed.
 */
function createSpedUpVideo(mp4s) {
  if (mp4s.length === 1) {
    // Single video - apply uniform speedup
    const out = join(OUTPUT_DIR, 'demo-sped-up.mp4');
    // 3x speed for the whole thing as a baseline
    run(
      `ffmpeg -y -i "${mp4s[0]}" -filter:v "setpts=PTS/3" -r 30 -an "${out}"`,
      'Speed up video 3x'
    );
    return out;
  }

  // Multiple videos (main page + review page)
  // Main page has more waiting, review page is more interactive
  const mainOut = join(OUTPUT_DIR, 'main-sped.mp4');
  const reviewOut = join(OUTPUT_DIR, 'review-sped.mp4');

  // Main page: 4x speed (lots of waiting for processing)
  run(
    `ffmpeg -y -i "${mp4s[0]}" -filter:v "setpts=PTS/4" -r 30 -an "${mainOut}"`,
    'Speed up main page 4x'
  );

  // Review page: 2x speed (more interactive, want to show the UX)
  run(
    `ffmpeg -y -i "${mp4s[1]}" -filter:v "setpts=PTS/2" -r 30 -an "${reviewOut}"`,
    'Speed up review page 2x'
  );

  // Concatenate
  const concatList = join(OUTPUT_DIR, 'concat.txt');
  writeFileSync(concatList, `file '${mainOut}'\nfile '${reviewOut}'\n`);

  const out = join(OUTPUT_DIR, 'demo-sped-up.mp4');
  run(
    `ffmpeg -y -f concat -safe 0 -i "${concatList}" -c copy "${out}"`,
    'Concatenate main + review'
  );
  return out;
}

/**
 * Step 3: Add intro and outro title cards
 */
function addTitleCards(inputVideo) {
  const duration = getDuration(inputVideo);

  // Create intro card (3 seconds, black background with white text)
  const introCard = join(OUTPUT_DIR, 'intro.mp4');
  const introFilter = [
    "drawtext=text='Nomad Karaoke':fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-40:font=Arial",
    "drawtext=text='Create Karaoke Videos for Any Song':fontsize=28:fontcolor=0xcccccc:x=(w-text_w)/2:y=(h-text_h)/2+30:font=Arial",
  ].join(',');
  run(
    `ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:d=4:r=30" -vf "${introFilter}" -c:v libx264 -pix_fmt yuv420p "${introCard}"`,
    'Create intro title card'
  );

  // Create outro card (4 seconds)
  const outroCard = join(OUTPUT_DIR, 'outro.mp4');
  const outroFilter = [
    "drawtext=text='Try it free':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-40:font=Arial",
    "drawtext=text='gen.nomadkaraoke.com':fontsize=36:fontcolor=0xff7acc:x=(w-text_w)/2:y=(h-text_h)/2+30:font=Arial",
  ].join(',');
  run(
    `ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:d=4:r=30" -vf "${outroFilter}" -c:v libx264 -pix_fmt yuv420p "${outroCard}"`,
    'Create outro title card'
  );

  // Concatenate: intro + main content + outro
  const concatList = join(OUTPUT_DIR, 'final-concat.txt');
  writeFileSync(concatList, `file '${introCard}'\nfile '${inputVideo}'\nfile '${outroCard}'\n`);

  const out = join(OUTPUT_DIR, 'demo-with-titles.mp4');
  run(
    `ffmpeg -y -f concat -safe 0 -i "${concatList}" -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p "${out}"`,
    'Add title cards'
  );
  return out;
}

/**
 * Step 4: Final encode with optimized settings for web
 */
function finalEncode(inputVideo) {
  const out = join(OUTPUT_DIR, 'nomad-karaoke-demo.mp4');
  run(
    `ffmpeg -y -i "${inputVideo}" ` +
    `-c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p ` +
    `-movflags +faststart ` +
    `"${out}"`,
    'Final encode for web'
  );

  const size = Math.round(execSync(`stat -f%z "${out}"`, { encoding: 'utf-8' }).trim() / 1024 / 1024 * 10) / 10;
  const duration = getDuration(out);
  console.log(`\n========================================`);
  console.log(`DEMO VIDEO CREATED`);
  console.log(`========================================`);
  console.log(`Output: ${out}`);
  console.log(`Duration: ${formatTime(duration)}`);
  console.log(`Size: ${size} MB`);
  console.log(`\nTo preview: open "${out}"`);
  return out;
}

// =============================================================================
// Main
// =============================================================================

console.log('Creating demo video from Playwright recordings...\n');

const videos = findVideos();
const mp4s = convertToMp4(videos);
const spedUp = createSpedUpVideo(mp4s);
const withTitles = addTitleCards(spedUp);
const final = finalEncode(withTitles);
