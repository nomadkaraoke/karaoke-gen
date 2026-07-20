# Image-time provisioning for the gpu-windows ephemeral GHA runner image.
#
# Runs as `windows-startup-script-ps1` on a fresh Windows Server 2022 VM with
# an NVIDIA T4 attached (created by .github/workflows/build-runner-images.yml).
#
# Installs:
#   - NVIDIA GRID driver (WDDM mode — REQUIRED for DirectML; the datacenter
#     driver runs the T4 in TCC mode which has no DirectX support)
#   - Python 3.12 (torch-directml has no 3.13 wheels), Git, FFmpeg, Poetry
#   - GitHub Actions runner (win-x64) at C:\actions-runner
#   - audio-separator DirectML test models at C:\audio-separator-models
#
# Conventions shared with runner-image-provision.sh (Linux):
#   - All output reaches the GCE serial console (the guest agent echoes
#     windows-startup-script-ps1 output to COM1), which the bake workflow
#     polls — no SSH/WinRM dependency.
#   - Success marker line:  "### runner-image: READY: ..."
#   - Failure marker line:  "### runner-image-provision FAILED"
#
# Idempotent: windows-startup-script-ps1 runs on EVERY boot, and the GRID
# driver step may need a reboot (TCC→WDDM switch). Completed phases are
# skipped via marker files under C:\provision-markers.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$VARIANT = "gpu-windows"
$PYTHON_VERSION = "3.12.10"
$GIT_VERSION = "2.45.2"
$RUNNER_VERSION = "2.334.0"   # keep in sync with runner-image-provision.sh

$MARKER_DIR = "C:\provision-markers"
$READY_FILE = "C:\runner-image-ready"
$WORK = "C:\provision-work"

function Ck([string]$msg) {
    Write-Output ""
    Write-Output "================================================================"
    Write-Output "### runner-image: $msg  ($(Get-Date -Format o))"
    Write-Output "================================================================"
}

function Phase-Done([string]$name) { Test-Path "$MARKER_DIR\$name" }
function Mark-Phase([string]$name) { New-Item -ItemType File -Force -Path "$MARKER_DIR\$name" | Out-Null }

function Download([string]$url, [string]$dest) {
    # Completed downloads are renamed from .part, so an existing $dest is
    # always complete (a reboot mid-download leaves only the .part file).
    if (Test-Path $dest) { return }
    Write-Output "  -> $(Split-Path -Leaf $dest)"
    $tmp = "$dest.part"
    & curl.exe -fSL --retry 3 --connect-timeout 30 -o $tmp $url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $url" }
    Move-Item -Force $tmp $dest
}

function Add-MachinePath([string]$dir) {
    $current = [Environment]::GetEnvironmentVariable("Path", "Machine")
    if ($current -notlike "*$dir*") {
        [Environment]::SetEnvironmentVariable("Path", "$current;$dir", "Machine")
    }
    $env:Path = "$env:Path;$dir"
}

# Already fully provisioned (post-reboot re-run after completion, or manual
# re-run): re-emit the READY marker for the serial-console poller and stop.
if (Test-Path $READY_FILE) {
    Ck "READY: variant=$VARIANT (already provisioned)"
    exit 0
}

New-Item -ItemType Directory -Force -Path $MARKER_DIR, $WORK | Out-Null

try {
    Ck "starting variant=$VARIANT"

    # ================= Long paths (pip/onnx exceed 260-char MAX_PATH) =====
    if (-not (Phase-Done "longpaths")) {
        Ck "phase: enable long paths"
        Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
            -Name LongPathsEnabled -Value 1 -Type DWord
        Mark-Phase "longpaths"
    }

    # ================= Windows Update off (bake determinism) ==============
    if (-not (Phase-Done "wu-off")) {
        Ck "phase: disable windows update"
        Stop-Service wuauserv -ErrorAction SilentlyContinue
        Set-Service wuauserv -StartupType Disabled -ErrorAction SilentlyContinue
        Mark-Phase "wu-off"
    }

    # ================= Defender exclusions (I/O speed) ====================
    if (-not (Phase-Done "defender")) {
        Ck "phase: defender exclusions"
        foreach ($p in @("C:\actions-runner", "C:\audio-separator-models", $WORK)) {
            Add-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue
        }
        Mark-Phase "defender"
    }

    # ================= NVIDIA GRID driver (WDDM for DirectML) =============
    if (-not (Phase-Done "grid-driver")) {
        Ck "phase: nvidia grid driver"
        # GCP hosts GRID installers in the public nvidia-drivers-us-public
        # bucket. Enumerate GRID*/ dirs newest-first and take the first one
        # containing a Windows Server 2022 installer.
        $api = "https://storage.googleapis.com/storage/v1/b/nvidia-drivers-us-public/o"
        # Object DOWNLOADS are anonymous, but LISTING requires any
        # authenticated Google identity (anonymous list = 401). Use the VM
        # service account's token from the metadata server.
        $mdToken = (Invoke-RestMethod -Headers @{ "Metadata-Flavor" = "Google" } `
            -Uri "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token").access_token
        $auth = "Authorization: Bearer $mdToken"
        $dirs = (& curl.exe -fsSL -H $auth "$api`?prefix=GRID/&delimiter=/" | ConvertFrom-Json).prefixes
        if (-not $dirs) { throw "could not list GRID driver directories" }
        # Sort by the leading numeric version (e.g. "GRID/GRID18.1/" -> 18.1);
        # tolerate any dir naming by falling back to 0 for non-numeric.
        $dirs = $dirs | Sort-Object {
            $m = [regex]::Match($_, '(\d+(\.\d+)?)')
            if ($m.Success) { [double]$m.Groups[1].Value } else { 0.0 }
        } -Descending
        $installerUrl = $null
        foreach ($d in $dirs) {
            $objs = (& curl.exe -fsSL -H $auth "$api`?prefix=$d" | ConvertFrom-Json).items
            $match = $objs | Where-Object { $_.name -like "*server2022*.exe" } | Select-Object -First 1
            if ($match) {
                $installerUrl = "https://storage.googleapis.com/nvidia-drivers-us-public/$($match.name)"
                break
            }
        }
        if (-not $installerUrl) { throw "no server2022 GRID installer found in nvidia-drivers-us-public" }
        Write-Output "GRID installer: $installerUrl"
        $installer = "$WORK\grid-driver.exe"
        Download $installerUrl $installer
        # -s: silent, -noreboot: we control reboots via the WDDM check below
        $p = Start-Process -FilePath $installer -ArgumentList "-s", "-noreboot" -Wait -PassThru
        if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 1) {  # 1 = success-needs-reboot
            throw "GRID driver installer exited with $($p.ExitCode)"
        }
        Mark-Phase "grid-driver"
        if ($p.ExitCode -eq 1) {
            Write-Output "GRID installer requests a reboot; rebooting before WDDM check"
            Restart-Computer -Force
            exit 0   # provisioning resumes on next boot
        }
    }

    # ================= Verify WDDM mode (DirectML requirement) ============
    if (-not (Phase-Done "wddm")) {
        Ck "phase: verify WDDM driver model"
        $smi = @(
            "C:\Windows\System32\nvidia-smi.exe",
            "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $smi) { throw "nvidia-smi not found after driver install" }
        $model = & $smi --query-gpu=driver_model.current --format=csv,noheader
        Write-Output "Driver model: $model"
        if ($model -notmatch "WDDM") {
            $attemptsFile = "$MARKER_DIR\wddm-attempts"
            $attempts = if (Test-Path $attemptsFile) { [int](Get-Content $attemptsFile) } else { 0 }
            if ($attempts -ge 3) { throw "WDDM switch did not take effect after $attempts reboot attempts" }
            Set-Content -Path $attemptsFile -Value ($attempts + 1)
            Write-Output "GPU is in TCC mode; switching to WDDM and rebooting (attempt $($attempts + 1)/3)"
            & $smi -fdm 0
            Restart-Computer -Force
            exit 0   # provisioning resumes on next boot
        }
        Mark-Phase "wddm"
    }

    # ================= Python 3.12 =======================================
    if (-not (Phase-Done "python")) {
        Ck "phase: python $PYTHON_VERSION"
        $exe = "$WORK\python-installer.exe"
        Download "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe" $exe
        $p = Start-Process -FilePath $exe -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_launcher=1" -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "python installer exited with $($p.ExitCode)" }
        Mark-Phase "python"
    }
    $py = "C:\Program Files\Python312\python.exe"
    if (-not (Test-Path $py)) { throw "python not found at $py" }

    # ================= Git ===============================================
    if (-not (Phase-Done "git")) {
        Ck "phase: git $GIT_VERSION"
        $exe = "$WORK\git-installer.exe"
        Download "https://github.com/git-for-windows/git/releases/download/v$GIT_VERSION.windows.1/Git-$GIT_VERSION-64-bit.exe" $exe
        $p = Start-Process -FilePath $exe -ArgumentList "/VERYSILENT", "/NORESTART" -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "git installer exited with $($p.ExitCode)" }
        Mark-Phase "git"
    }

    # ================= FFmpeg ============================================
    if (-not (Phase-Done "ffmpeg")) {
        Ck "phase: ffmpeg"
        $zip = "$WORK\ffmpeg.zip"
        Download "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $zip
        Expand-Archive -Path $zip -DestinationPath $WORK\ffmpeg-extract -Force
        $bin = Get-ChildItem "$WORK\ffmpeg-extract" -Recurse -Filter ffmpeg.exe | Select-Object -First 1
        if (-not $bin) { throw "ffmpeg.exe not found in archive" }
        New-Item -ItemType Directory -Force -Path "C:\ffmpeg\bin" | Out-Null
        Copy-Item "$($bin.DirectoryName)\*" "C:\ffmpeg\bin" -Force
        Add-MachinePath "C:\ffmpeg\bin"
        Mark-Phase "ffmpeg"
    }

    # ================= Poetry (system-wide, SYSTEM-user friendly) =========
    if (-not (Phase-Done "poetry")) {
        Ck "phase: poetry"
        # pip install into the all-users Python — its Scripts dir is already
        # on the machine PATH, and works for jobs running as any user
        # (pipx would land in the SYSTEM profile and break for others).
        & $py -m pip install --no-input --quiet poetry
        if ($LASTEXITCODE -ne 0) { throw "pip install poetry failed" }
        Mark-Phase "poetry"
    }

    # ================= GitHub Actions runner ==============================
    if (-not (Phase-Done "runner")) {
        Ck "phase: actions runner v$RUNNER_VERSION"
        $zip = "$WORK\actions-runner.zip"
        Download "https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-win-x64-$RUNNER_VERSION.zip" $zip
        New-Item -ItemType Directory -Force -Path "C:\actions-runner" | Out-Null
        Expand-Archive -Path $zip -DestinationPath "C:\actions-runner" -Force
        if (-not (Test-Path "C:\actions-runner\run.cmd")) { throw "runner extract failed" }
        Mark-Phase "runner"
    }

    # ================= audio-separator DirectML test models ===============
    # Lean set: only what the windows-directml integration job uses (the
    # Linux GPU image bakes the full ~14GB suite; Windows doesn't run the
    # ensemble/demucs tests).
    if (-not (Phase-Done "models")) {
        Ck "phase: audio-separator models (DirectML test set)"
        $modelDir = "C:\audio-separator-models"
        New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
        $BASE = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models"
        $CONF = "https://raw.githubusercontent.com/TRvlvr/application_data/main/mdx_model_data/mdx_c_configs"
        $META = "https://raw.githubusercontent.com/TRvlvr/application_data/main"
        $FALLBACK = "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs"

        # Metadata (same filenames as the Linux image)
        Download "$META/filelists/download_checks.json" "$modelDir\download_checks.json"
        Download "$META/vr_model_data/model_data_new.json" "$modelDir\vr_model_data.json"
        Download "$META/mdx_model_data/model_data_new.json" "$modelDir\mdx_model_data.json"

        # RoFormer (the point of this image) + MDX + VR regression guards
        Download "$BASE/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt" "$modelDir\mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
        Download "$FALLBACK/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956_config.yaml" "$modelDir\mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956_config.yaml"
        Download "$BASE/model_bs_roformer_ep_317_sdr_12.9755.ckpt" "$modelDir\model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        Download "$CONF/model_bs_roformer_ep_317_sdr_12.9755.yaml" "$modelDir\model_bs_roformer_ep_317_sdr_12.9755.yaml"
        Download "$BASE/UVR-MDX-NET-Inst_HQ_4.onnx" "$modelDir\UVR-MDX-NET-Inst_HQ_4.onnx"
        Download "$BASE/2_HP-UVR.pth" "$modelDir\2_HP-UVR.pth"
        Mark-Phase "models"
    }

    # ================= Done ==============================================
    New-Item -ItemType File -Force -Path $READY_FILE | Out-Null
    Ck "READY: variant=$VARIANT  ts=$(Get-Date -Format o)"
}
catch {
    Write-Output ""
    Write-Output "########################################################################"
    Write-Output "### runner-image-provision FAILED"
    Write-Output "###   error=$($_.Exception.Message)"
    Write-Output "###   at=$($_.InvocationInfo.PositionMessage)"
    Write-Output "###   variant=$VARIANT  ts=$(Get-Date -Format o)"
    Write-Output "########################################################################"
    exit 1
}
