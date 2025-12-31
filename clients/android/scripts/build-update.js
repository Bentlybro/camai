#!/usr/bin/env node
/**
 * CAMAI Update Builder
 * Builds an APK and prepares it for OTA distribution
 *
 * Usage: bun run update [patch|minor|major] [--notes "Release notes"]
 */

import { existsSync, readFileSync, writeFileSync, copyFileSync, statSync, mkdirSync } from 'fs';
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = join(__dirname, '..');
const RELEASES_DIR = join(PROJECT_ROOT, '..', '..', 'releases');

// File paths
const PACKAGE_JSON = join(PROJECT_ROOT, 'package.json');
const APP_JS = join(PROJECT_ROOT, 'www', 'js', 'app.js');
const VERSION_JSON = join(RELEASES_DIR, 'version.json');
const APK_OUTPUT = join(PROJECT_ROOT, 'android', 'app', 'build', 'outputs', 'apk', 'release', 'app-release-unsigned.apk');
const APK_DEBUG = join(PROJECT_ROOT, 'android', 'app', 'build', 'outputs', 'apk', 'debug', 'app-debug.apk');

// Colors for console output
const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  red: '\x1b[31m',
  cyan: '\x1b[36m',
};

function log(msg, color = colors.reset) {
  console.log(`${color}${msg}${colors.reset}`);
}

function logStep(step, msg) {
  console.log(`${colors.cyan}[${step}]${colors.reset} ${msg}`);
}

function logSuccess(msg) {
  console.log(`${colors.green}✓${colors.reset} ${msg}`);
}

function logError(msg) {
  console.log(`${colors.red}✗${colors.reset} ${msg}`);
}

// Parse version string to parts
function parseVersion(version) {
  const match = version.match(/^(\d+)\.(\d+)\.(\d+)$/);
  if (!match) throw new Error(`Invalid version: ${version}`);
  return {
    major: parseInt(match[1]),
    minor: parseInt(match[2]),
    patch: parseInt(match[3]),
  };
}

// Increment version
function incrementVersion(version, type = 'patch') {
  const parts = parseVersion(version);
  switch (type) {
    case 'major':
      parts.major++;
      parts.minor = 0;
      parts.patch = 0;
      break;
    case 'minor':
      parts.minor++;
      parts.patch = 0;
      break;
    case 'patch':
    default:
      parts.patch++;
      break;
  }
  return `${parts.major}.${parts.minor}.${parts.patch}`;
}

// Calculate version code from version string
function versionToCode(version) {
  const parts = parseVersion(version);
  return parts.major * 10000 + parts.minor * 100 + parts.patch;
}

// Update version in package.json
function updatePackageJson(newVersion) {
  const pkg = JSON.parse(readFileSync(PACKAGE_JSON, 'utf8'));
  pkg.version = newVersion;
  writeFileSync(PACKAGE_JSON, JSON.stringify(pkg, null, 2) + '\n');
  return pkg;
}

// Update version in app.js
function updateAppJs(newVersion, newVersionCode) {
  let content = readFileSync(APP_JS, 'utf8');

  // Update APP_VERSION constant
  content = content.replace(
    /const APP_VERSION = '[^']+'/,
    `const APP_VERSION = '${newVersion}'`
  );

  // Update APP_VERSION_CODE constant
  content = content.replace(
    /const APP_VERSION_CODE = \d+/,
    `const APP_VERSION_CODE = ${newVersionCode}`
  );

  writeFileSync(APP_JS, content);
}

// Update version.json for OTA
function updateVersionJson(newVersion, newVersionCode, releaseNotes, apkSize) {
  const versionInfo = {
    version: newVersion,
    version_code: newVersionCode,
    release_notes: releaseNotes,
    apk_filename: `camai-${newVersion}.apk`,
    apk_size: apkSize,
    required: false,
  };

  // Ensure releases directory exists
  if (!existsSync(RELEASES_DIR)) {
    mkdirSync(RELEASES_DIR, { recursive: true });
  }

  writeFileSync(VERSION_JSON, JSON.stringify(versionInfo, null, 4) + '\n');
  return versionInfo;
}

// Run shell command
function run(cmd, cwd = PROJECT_ROOT) {
  try {
    execSync(cmd, { cwd, stdio: 'inherit', shell: true });
    return true;
  } catch (error) {
    return false;
  }
}

// Main build function
async function main() {
  const args = process.argv.slice(2);

  // Parse arguments
  let incrementType = 'patch';
  let releaseNotes = 'Bug fixes and improvements';
  let useDebug = false;

  for (let i = 0; i < args.length; i++) {
    if (['patch', 'minor', 'major'].includes(args[i])) {
      incrementType = args[i];
    } else if (args[i] === '--notes' && args[i + 1]) {
      releaseNotes = args[++i];
    } else if (args[i] === '--debug') {
      useDebug = true;
    }
  }

  log('\n========================================', colors.cyan);
  log('       CAMAI Update Builder', colors.bright);
  log('========================================\n', colors.cyan);

  // Step 1: Read current version
  logStep('1/7', 'Reading current version...');
  const pkg = JSON.parse(readFileSync(PACKAGE_JSON, 'utf8'));
  const currentVersion = pkg.version;
  const newVersion = incrementVersion(currentVersion, incrementType);
  const newVersionCode = versionToCode(newVersion);

  log(`  Current: ${colors.yellow}${currentVersion}${colors.reset}`);
  log(`  New:     ${colors.green}${newVersion}${colors.reset} (code: ${newVersionCode})`);
  log(`  Type:    ${incrementType}`);
  log(`  Notes:   ${releaseNotes}\n`);

  // Step 2: Update version files
  logStep('2/7', 'Updating version in package.json...');
  updatePackageJson(newVersion);
  logSuccess('package.json updated');

  logStep('3/7', 'Updating version in app.js...');
  updateAppJs(newVersion, newVersionCode);
  logSuccess('app.js updated');

  // Step 4: Sync Capacitor
  logStep('4/7', 'Syncing Capacitor...');
  if (!run('bunx cap sync android')) {
    logError('Capacitor sync failed');
    process.exit(1);
  }
  logSuccess('Capacitor synced');

  // Step 5: Build APK
  logStep('5/7', `Building ${useDebug ? 'debug' : 'release'} APK...`);
  const gradleCmd = process.platform === 'win32' ? 'gradlew.bat' : './gradlew';
  const buildTask = useDebug ? 'assembleDebug' : 'assembleRelease';
  const androidDir = join(PROJECT_ROOT, 'android');

  if (!run(`${gradleCmd} ${buildTask}`, androidDir)) {
    logError('APK build failed');
    process.exit(1);
  }
  logSuccess('APK built successfully');

  // Step 6: Copy APK to releases
  logStep('6/7', 'Copying APK to releases folder...');
  const sourceApk = useDebug ? APK_DEBUG : APK_OUTPUT;

  // Also check for signed release APK
  const signedApk = join(PROJECT_ROOT, 'android', 'app', 'build', 'outputs', 'apk', 'release', 'app-release.apk');
  const actualSourceApk = existsSync(signedApk) ? signedApk : (existsSync(sourceApk) ? sourceApk : null);

  if (!actualSourceApk) {
    logError(`APK not found at expected locations`);
    log(`  Checked: ${sourceApk}`);
    log(`  Checked: ${signedApk}`);
    process.exit(1);
  }

  const destApk = join(RELEASES_DIR, `camai-${newVersion}.apk`);

  // Ensure releases directory exists
  if (!existsSync(RELEASES_DIR)) {
    mkdirSync(RELEASES_DIR, { recursive: true });
  }

  copyFileSync(actualSourceApk, destApk);
  const apkSize = statSync(destApk).size;
  logSuccess(`APK copied: ${destApk}`);
  log(`  Size: ${(apkSize / 1024 / 1024).toFixed(2)} MB`);

  // Step 7: Update version.json
  logStep('7/7', 'Updating version.json...');
  const versionInfo = updateVersionJson(newVersion, newVersionCode, releaseNotes, apkSize);
  logSuccess('version.json updated');

  // Done!
  log('\n========================================', colors.green);
  log('         Build Complete!', colors.bright);
  log('========================================\n', colors.green);

  log('Summary:', colors.bright);
  log(`  Version:  ${newVersion} (code: ${newVersionCode})`);
  log(`  APK:      camai-${newVersion}.apk`);
  log(`  Size:     ${(apkSize / 1024 / 1024).toFixed(2)} MB`);
  log(`  Notes:    ${releaseNotes}`);
  log(`  Location: ${RELEASES_DIR}\n`);

  log('Next steps:', colors.yellow);
  log('  1. Test the APK on a device');
  log('  2. The update will be available to all connected apps');
  log('  3. Users will see the update prompt on app launch\n');
}

main().catch(err => {
  logError(err.message);
  process.exit(1);
});
